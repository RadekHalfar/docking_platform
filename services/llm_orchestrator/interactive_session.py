"""Interactive workflow session helpers."""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, cast

from common.file_io import atomic_write_json
from common.job_log import append_job_log
from common.param_diff import changed_parameter_keys
from common.utils import ensure_shared_directory
from .workflow_definition import get_ligand_display_names, get_protein_display_name, with_default_parameters
from .workflow_state_manager import WorkflowStateManager

logger = logging.getLogger(__name__)


def compute_changed_params(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Set[str]:
    """Return the set of parameter keys that changed between two snapshots.

    Thin wrapper around :func:`common.param_diff.changed_parameter_keys` -- the
    GUI's one-shot equivalent of how the CLI's ``InteractiveSession``
    incrementally accumulates ``pending_changed_params`` across individual
    ``set ...`` commands: the GUI submits the whole form at once and diffs it
    against the last on-disk checkpoint. Since Stage 13.2 the GUI also keeps
    ``pending_changed_params`` for changes already checkpointed but not yet
    run to completion (a restored revision, a cancelled redock plan), which
    that diff can't see.

    Both sides get the defaults filled in (:func:`with_default_parameters`),
    so a key one side lacks -- a form without energy_range, a checkpoint
    from before docking_algorithm existed -- isn't a change. With no prior
    parameters (*old* is None) every key in *new* still counts as changed.
    """
    if old is None:
        return set(changed_parameter_keys(None, new))
    return set(changed_parameter_keys(with_default_parameters(old), with_default_parameters(new)))


def resolve_prepared_protein_pdb_path(last_results: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the prepared receptor PDB path from a workflow results dict.

    *last_results* is the ``workflow_results.json``-shaped dict
    (``InteractiveSession.last_results`` / a GUI bridge's ``get_result()``):
    ``{"results": {"prepare_protein": {"prepared_proteins": [...]}}, ...}``.
    Used by both the GUI chat (``ui/core/box_picker.py``) and the CLI's interactive
    session (``nlp_orchestrator.py``) to find the file to read atom
    coordinates from for the "set box center as protein center" / "look
    across all protein" commands -- one definition of "where is the
    prepared protein" instead of two independently-maintained dict-walks.
    Returns ``None`` on any missing/empty step along the way rather than
    raising, since "no protein prepared yet" is an expected, common state.
    """
    if not last_results:
        return None
    prepared = last_results.get("results", {}).get("prepare_protein", {})
    proteins = prepared.get("prepared_proteins") or []
    if not proteins:
        return None
    return proteins[0].get("pdb_path")


# ---------------------------------------------------------------------------
# Bundle name validation
# ---------------------------------------------------------------------------

SAVED_RESULTS_DIRNAME = "saved_results"
_MAX_BUNDLE_NAME_LEN = 64
_VALID_BUNDLE_NAME_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{_MAX_BUNDLE_NAME_LEN - 1}}}$")
_RESERVED_NAMES: frozenset = frozenset(
    {"checkpoints", "latest", "downloads", "prepared", "docking_results", "visualization"}
)


def validate_bundle_name(name: str) -> Optional[str]:
    """Return a human-readable error string if *name* is invalid, else ``None``."""
    if not name or not name.strip():
        return "Bundle name cannot be empty."
    if ".." in name or "/" in name or "\\" in name:
        return "Bundle name must not contain path separators or '..'."
    if not _VALID_BUNDLE_NAME_RE.match(name):
        return (
            "Bundle name must start with a letter or digit and contain only "
            f"letters, digits, '.', '-', '_' (max {_MAX_BUNDLE_NAME_LEN} characters)."
        )
    if name.lower() in _RESERVED_NAMES:
        return f"'{name}' is a reserved directory name. Please choose another name."
    return None


def timestamped_bundle_name(job_dir: Path, name: str) -> str:
    """Return a free ``<name>_HHMMSS`` bundle name for *job_dir*.

    The fallback when *name* already exists (CLI and GUI). *name* is cut
    short so the result still passes :func:`validate_bundle_name`'s length
    limit, and ``_2``, ``_3``, ... is appended while the name is taken (two
    saves within the same second would otherwise collide).
    """
    suffix = datetime.now(timezone.utc).strftime("%H%M%S")
    saved = Path(job_dir) / SAVED_RESULTS_DIRNAME
    counter = 1
    while True:
        tail = f"_{suffix}" if counter == 1 else f"_{suffix}_{counter}"
        candidate = name[: _MAX_BUNDLE_NAME_LEN - len(tail)] + tail
        if not (saved / candidate).exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Bundle materialisation
# ---------------------------------------------------------------------------

def save_results_bundle(
    job_dir: Path,
    name: str,
    scope: str,
    revision: int,
    parameters: Dict[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy current docking/visualization artifacts into a named saved-results bundle.

    Args:
        job_dir:    The active job directory (e.g. ``orchestrator_output/job_<id>``).
        name:       User-supplied bundle name (validated before use).
        scope:      One of ``"docking"``, ``"visualization"``, or ``"all"``.
        revision:   Current parameter revision number (captured in metadata).
        parameters: Active workflow parameters snapshot.
        overwrite:  When *True*, replace an existing bundle with the same name
                    (the old bundle is removed first, not merged into).

    Returns:
        A dict with keys:

        * ``success`` (bool)
        * ``bundle_path`` (str | None)
        * ``artifacts`` (list[str]) — names of sub-directories/files copied
        * ``error`` (str | None) — human-readable reason when ``success`` is
          False; exactly ``"exists"`` when the name is taken and *overwrite*
          is False (:func:`timestamped_bundle_name` gives the fallback name).
          A filesystem error while writing is reported here too, with the
          partial bundle removed.
    """
    job_dir = Path(job_dir)

    err = validate_bundle_name(name)
    if err:
        append_job_log(job_dir, "ERROR", f"save_results_bundle '{name}' rejected: {err}")
        return {"success": False, "bundle_path": None, "artifacts": [], "error": err}

    bundle_dir = job_dir / SAVED_RESULTS_DIRNAME / name

    if bundle_dir.exists() and not overwrite:
        append_job_log(job_dir, "ERROR", f"save_results_bundle '{name}' failed: bundle already exists")
        return {
            "success": False,
            "bundle_path": str(bundle_dir),
            "artifacts": [],
            "error": "exists",
        }

    # The bundle is written next to its final place and only then swapped in,
    # so a failed overwrite (disk full) keeps the old bundle instead of
    # destroying it (Review Stage R1, Phase 13). Valid bundle names can't
    # start with '.', so these never collide with a real bundle.
    staging_dir = bundle_dir.with_name(f".{name}.partial")
    replaced_dir = bundle_dir.with_name(f".{name}.replaced")
    try:
        for leftover in (staging_dir, replaced_dir):
            shutil.rmtree(leftover, ignore_errors=True)
        # Shared: sessions the former root Streamlit GUI created get
        # bundles from the non-root GUI too (Phase 16).
        ensure_shared_directory(bundle_dir.parent)
        staging_dir.mkdir()
        included = _write_bundle_contents(job_dir, staging_dir, name, scope, revision, parameters)
        # The bundle's own directories too: replacing a bundle deletes its
        # files, which needs write access to every directory in it. Without
        # this, a bundle the other GUI's uid saved (0o755 copytree dirs)
        # can't be overwritten, and the failed cleanup then blocks every
        # later overwrite of that name (Review Stage R2).
        for directory in (staging_dir, *(d for d in staging_dir.rglob("*") if d.is_dir())):
            ensure_shared_directory(directory)
        # Overwrite replaces the whole bundle: merging would keep e.g. an
        # older "all" save's visualization/ under metadata that now says
        # scope=docking.
        if bundle_dir.exists():
            bundle_dir.rename(replaced_dir)
        staging_dir.rename(bundle_dir)
    except OSError as exc:
        # Filesystem boundary (disk full, permissions): report a failed save
        # instead of crashing the caller, leave no half-written bundle
        # behind, and put a replaced bundle back.
        shutil.rmtree(staging_dir, ignore_errors=True)
        if replaced_dir.exists() and not bundle_dir.exists():
            replaced_dir.rename(bundle_dir)
        append_job_log(job_dir, "ERROR", f"save_results_bundle '{name}' failed: {exc}")
        return {"success": False, "bundle_path": None, "artifacts": [], "error": f"Could not write bundle: {exc}"}
    shutil.rmtree(replaced_dir, ignore_errors=True)

    append_job_log(
        job_dir, "INFO",
        f"save_results_bundle '{name}' saved (scope={scope}, revision={revision}): {', '.join(included) or 'nothing to copy'}",
    )

    return {
        "success": True,
        "bundle_path": str(bundle_dir),
        "artifacts": included,
        "error": None,
    }


def _write_bundle_contents(
    job_dir: Path,
    bundle_dir: Path,
    name: str,
    scope: str,
    revision: int,
    parameters: Dict[str, Any],
) -> List[str]:
    """Copy the scoped artifacts plus docking_box.json/metadata.json into the
    (freshly created) *bundle_dir*; return the names of what was included."""
    included: List[str] = []
    include_docking = scope in ("docking", "all")
    include_viz = scope in ("visualization", "all")

    if include_docking:
        src = job_dir / "docking_results"
        if src.exists() and any(src.iterdir()):
            shutil.copytree(src, bundle_dir / "docking_results")
            included.append("docking_results")
        else:
            logger.debug("save_results_bundle: no docking results found in %s", src)

    if include_viz:
        src = job_dir / "visualization"
        if src.exists() and any(src.iterdir()):
            shutil.copytree(src, bundle_dir / "visualization")
            included.append("visualization")
        else:
            logger.debug("save_results_bundle: no visualization artifacts found in %s", src)

    # Write docking_box.json from current parameters — always authoritative.
    # This guarantees the bundle reflects the box that was actually used for
    # docking, even when the user changed center/box_size via a text command
    # after docking_box.json was last written to disk.
    _center = parameters.get("center")
    _box_size = parameters.get("box_size")
    if _center and _box_size:
        box_payload: Dict[str, Any] = {
            "job_id": job_dir.name,
            "center": _center,
            "box_size": _box_size,
            "source": "parameters_snapshot",
        }
        # Preserve original source tag if the disk file is present and both
        # center/box_size values match (i.e. nothing was changed manually).
        _disk_box_file = job_dir / "docking_box.json"
        if _disk_box_file.exists():
            try:
                import json as _json
                _disk = _json.loads(_disk_box_file.read_text(encoding="utf-8"))
                if _disk.get("center") == list(_center) and _disk.get("box_size") == list(_box_size):
                    box_payload["source"] = _disk.get("source", "parameters_snapshot")
            except Exception:
                pass
        atomic_write_json(bundle_dir / "docking_box.json", box_payload)
        included.append("docking_box.json")
    else:
        # No box params in current parameters — fall back to copying from disk.
        _disk_box_file = job_dir / "docking_box.json"
        if _disk_box_file.exists():
            shutil.copy2(_disk_box_file, bundle_dir / "docking_box.json")
            included.append("docking_box.json")

    metadata: Dict[str, Any] = {
        "name": name,
        "job_id": job_dir.name,
        "source_revision": revision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "artifacts": included,
        "parameters": parameters,
    }
    atomic_write_json(bundle_dir / "metadata.json", metadata)
    return included


@dataclass
class InteractiveSession:
    """Holds active session context for interactive CLI usage."""

    job_id: Optional[str] = None
    state_manager: Optional[WorkflowStateManager] = None
    last_results: Optional[Dict[str, Any]] = None
    session_active: bool = True
    pending_changed_params: Set[str] = field(default_factory=set)

    def has_active_job(self) -> bool:
        """Return True when a job is currently attached to this session."""
        return bool(self.job_id and self.state_manager)

    def activate(self, job_id: str, state_manager: WorkflowStateManager, last_results: Optional[Dict[str, Any]] = None) -> None:
        """Attach a job/state manager as the active session context.

        Resets ``pending_changed_params``: changes pending against the
        previously attached job say nothing about this one (the GUI reuses
        one session object across sidebar Loads).
        """
        self.job_id = job_id
        self.state_manager = state_manager
        self.last_results = last_results
        self.pending_changed_params = set()

    def clear(self) -> None:
        """Clear active job context."""
        self.job_id = None
        self.state_manager = None
        self.last_results = None
        self.pending_changed_params = set()

    def state_snapshot(self) -> Dict[str, Any]:
        """Return disk-backed current snapshot for active job."""
        if not self.has_active_job():
            return {
                "active": False,
                "job_id": None,
                "revision": None,
                "parameters": {},
                "runtime_state": {},
                "last_status": None,
            }

        sm = cast(WorkflowStateManager, self.state_manager)
        return {
            "active": True,
            "job_id": self.job_id,
            "revision": sm.get_current_revision(),
            "parameters": sm.load_latest_parameters() or {},
            "runtime_state": sm.load_latest_state() or {},
            "last_status": "parameters_changed" if self.pending_changed_params else (self.last_results or {}).get("status"),
        }

    def format_state_summary(self) -> str:
        """Return a human-friendly summary of current interactive session state."""
        snapshot = self.state_snapshot()
        if not snapshot["active"]:
            return "No active job in this session yet."

        lines = [
            "Session State:",
            f"  Job ID: {snapshot['job_id']}",
            f"  Revision: {snapshot['revision']}",
            f"  Last Status: {snapshot['last_status'] or 'unknown'}",
        ]

        params = snapshot["parameters"]
        if params:
            lines.append("  Parameters:")
            protein_display = get_protein_display_name(params)
            if protein_display:
                lines.append(f"    protein_id: {protein_display}")
            ligand_display = get_ligand_display_names(params)
            if ligand_display:
                lines.append(f"    ligand_ids: {ligand_display}")
            if "center" in params:
                lines.append(f"    center: {params['center']}")
            if "box_size" in params:
                lines.append(f"    box_size: {params['box_size']}")

        runtime_state = snapshot["runtime_state"]
        if runtime_state:
            lines.append("  Runtime State Keys: " + ", ".join(sorted(runtime_state.keys())))

        return "\n".join(lines)
