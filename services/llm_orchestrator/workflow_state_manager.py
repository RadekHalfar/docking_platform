"""
Workflow state persistence utilities.

Hybrid model:
- "latest" files for fast runtime loads
- immutable revision checkpoints for audit/recovery
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.file_io import atomic_write_json, load_json_dict

logger = logging.getLogger(__name__)


class WorkflowStateManager:
    """Persists workflow parameters/state with revisioned checkpoints."""

    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)

        self.parameters_file = self.job_dir / "workflow_parameters.json"
        self.state_file = self.job_dir / "workflow_state.json"
        self.manifest_file = self.job_dir / "workflow_manifest.json"
        self.checkpoints_dir = self.job_dir / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        if not self.manifest_file.exists():
            self._write_manifest({"current_revision": 0, "events": []})

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_manifest(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize manifest payload shape with required defaults."""
        if not isinstance(manifest, dict):
            return {"current_revision": 0, "events": []}
        manifest.setdefault("current_revision", 0)
        manifest.setdefault("events", [])
        return manifest

    def _read_manifest(self) -> Dict[str, Any]:
        return self._normalize_manifest(load_json_dict(self.manifest_file))

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        atomic_write_json(self.manifest_file, self._normalize_manifest(manifest))

    def _checkpoint_path(self, revision: int, suffix: str) -> Path:
        """Return the canonical checkpoint file path for a given revision and type suffix."""
        return self.checkpoints_dir / f"rev_{revision:06d}_{suffix}.json"

    def _next_revision(self, event_type: str, details: Dict[str, Any]) -> int:
        manifest = self._read_manifest()
        revision = int(manifest.get("current_revision", 0)) + 1
        manifest["current_revision"] = revision

        event = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "event_type": event_type,
            "details": details,
        }
        events = manifest.setdefault("events", [])
        events.append(event)
        self._write_manifest(manifest)
        return revision

    def get_current_revision(self) -> int:
        manifest = self._read_manifest()
        return int(manifest.get("current_revision", 0))

    def save_parameters_checkpoint(self, parameters: Dict[str, Any], source: str = "unknown") -> int:
        """Persist latest parameters and snapshot a new immutable revision."""
        revision = self._next_revision("parameters_update", {"source": source})
        payload = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "source": source,
            "parameters": parameters,
        }

        atomic_write_json(self.parameters_file, payload)
        atomic_write_json(self._checkpoint_path(revision, "parameters"), payload)
        logger.info(f"Saved parameter checkpoint revision={revision} source={source}")
        return revision

    def save_execution_state_checkpoint(self, runtime_state: Dict[str, Any], step: str) -> int:
        """Persist latest runtime state and snapshot a new immutable revision."""
        revision = self._next_revision("state_update", {"step": step})
        payload = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "step": step,
            "state": runtime_state,
        }

        atomic_write_json(self.state_file, payload)
        atomic_write_json(self._checkpoint_path(revision, "state"), payload)
        logger.info(f"Saved state checkpoint revision={revision} step={step}")
        return revision

    def _load_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        data = load_json_dict(path)
        if data is None and path.exists():
            logger.warning(f"Failed to load JSON file {path}")
        return data

    def load_latest_parameters(self) -> Optional[Dict[str, Any]]:
        """Load latest parameters (disk is source-of-truth when present)."""
        payload = self._load_json_file(self.parameters_file)
        if not payload:
            return None
        params = payload.get("parameters")
        if isinstance(params, dict):
            return params
        return None

    def load_latest_state(self) -> Optional[Dict[str, Any]]:
        """Load latest runtime state snapshot."""
        payload = self._load_json_file(self.state_file)
        if not payload:
            return None
        state = payload.get("state")
        if isinstance(state, dict):
            return state
        return None

    # ------------------------------------------------------------------
    # Phase 4: revision history and undo/restore
    # ------------------------------------------------------------------

    def list_revisions(self) -> List[Dict[str, Any]]:
        """Return a list of all recorded revision events, oldest first.

        Each entry is a dict with keys:
          ``revision`` (int), ``timestamp`` (str), ``event_type`` (str),
          ``details`` (dict), ``has_parameters`` (bool), ``has_state`` (bool).
        """
        manifest = self._read_manifest()
        events: List[Dict[str, Any]] = manifest.get("events", [])

        enriched = []
        for event in events:
            rev = int(event.get("revision", 0))
            enriched.append(
                {
                    "revision": rev,
                    "timestamp": event.get("timestamp", ""),
                    "event_type": event.get("event_type", ""),
                    "details": event.get("details", {}),
                    "has_parameters": self._checkpoint_path(rev, "parameters").exists(),
                    "has_state": self._checkpoint_path(rev, "state").exists(),
                }
            )
        return enriched

    def load_checkpoint(self, revision: int) -> Dict[str, Any]:
        """Load both the parameters and state checkpoint for *revision*.

        Returns a dict with keys:
          ``revision`` (int), ``parameters`` (dict or None),
          ``runtime_state`` (dict or None).

        Raises ``ValueError`` when no checkpoint files exist for that revision.
        """
        params_file = self._checkpoint_path(revision, "parameters")
        state_file = self._checkpoint_path(revision, "state")

        if not params_file.exists() and not state_file.exists():
            raise ValueError(
                f"No checkpoint files found for revision {revision}. "
                f"Available revisions: {self._available_revisions()}"
            )

        parameters: Optional[Dict[str, Any]] = None
        if params_file.exists():
            payload = self._load_json_file(params_file)
            if payload:
                parameters = payload.get("parameters")

        runtime_state: Optional[Dict[str, Any]] = None
        if state_file.exists():
            payload = self._load_json_file(state_file)
            if payload:
                runtime_state = payload.get("state")

        return {
            "revision": revision,
            "parameters": parameters,
            "runtime_state": runtime_state,
        }

    def restore_revision(self, revision: int) -> Dict[str, Any]:
        """Promote checkpoint *revision* to the "latest" files and record an undo event.

        Only components that have a checkpoint at *revision* are restored:
        - if a parameters checkpoint exists, it overwrites ``workflow_parameters.json``
        - if a state checkpoint exists, it overwrites ``workflow_state.json``

        A new revision is recorded so the restore itself is auditable.

        Returns the same dict as :meth:`load_checkpoint`.
        """
        data = self.load_checkpoint(revision)  # raises ValueError for invalid revision

        new_revision = self._next_revision(
            "undo_restore",
            {"restored_from_revision": revision},
        )

        if data["parameters"] is not None:
            payload = {
                "revision": new_revision,
                "timestamp": self._timestamp(),
                "source": f"undo_restore_from_rev_{revision}",
                "parameters": data["parameters"],
            }
            atomic_write_json(self.parameters_file, payload)
            atomic_write_json(self._checkpoint_path(new_revision, "parameters"), payload)

        if data["runtime_state"] is not None:
            payload = {
                "revision": new_revision,
                "timestamp": self._timestamp(),
                "step": f"undo_restore_from_rev_{revision}",
                "state": data["runtime_state"],
            }
            atomic_write_json(self.state_file, payload)
            atomic_write_json(self._checkpoint_path(new_revision, "state"), payload)

        logger.info(
            f"Restored revision={revision} → promoted to new revision={new_revision}"
        )
        return data

    def _available_revisions(self) -> List[int]:
        """Return sorted list of revision numbers that have at least one checkpoint file."""
        revisions: set = set()
        for f in self.checkpoints_dir.glob("rev_*_*.json"):
            try:
                revisions.add(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return sorted(revisions)
