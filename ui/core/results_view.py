"""What the results panel shows, as data (Phase 16, Stage 16.2).

The widgets stay in the GUI. Paths in a workflow result are the MCP
services' own (``/app/orchestrator_output/...``) and are mapped onto
*output_base* with ``common.path_utils.from_container_path``.

Helpers that read result files take *read_bytes*, so a GUI can pass a
cached reader.

Stage 16.6 adds what NiceGUI's Results tab needs: :func:`read_results_data`
does all of one result's file reads at once, so a GUI with an event loop
runs it off the loop once per result, not per redraw or timer tick;
:func:`job_log_if_changed` re-reads ``job.log`` only when it has changed;
and :func:`resolve_job_file` / :func:`job_file_url` are the download
route's path checks.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.parse import quote

from common.docking_algorithm import docking_algorithm_label, docking_algorithm_of
from common.file_io import load_json_dict
from common.path_utils import from_container_path
from services.llm_orchestrator.interactive_session import SAVED_RESULTS_DIRNAME
from ui.job_history import escape_markdown

ReadBytes = Callable[[Path], bytes]

# The Session Log viewer shows the log's last lines; the download has all of it.
JOB_LOG_TAIL_LINES = 500

POSE_DOWNLOAD_FORMATS = ["pdbqt", "pdb", "sdf", "mol2"]

# "Other Files" offers only these; the poses have their own downloads.
OTHER_DOWNLOAD_SUFFIXES = {".pml", ".json", ".png"}


def _read_path(path: Path) -> bytes:
    # Uncached: the GUI reads a result once (read_results_data) and job.log only when
    # it changed (job_log_if_changed) -- Review Stage R1, deferred #3.
    return path.read_bytes()


def job_label(job_id: str, job_name: Optional[str]) -> str:
    """Markdown label for a job header: bold name (if any) plus the id."""
    if job_name:
        return f"**{escape_markdown(job_name)}** (`{job_id}`)"
    return f"`{job_id}`"


def docked_with_caption(result: dict) -> Optional[str]:
    """"Docked with <algorithm>", or None if the result has no dock step."""
    dock = result.get("results", {}).get("dock")
    if not dock:
        return None
    return f"Docked with {docking_algorithm_label(docking_algorithm_of(dock))}"


def read_docking_progress(output_base: Path, job_id: str) -> Optional[dict]:
    """The dock step's ``docking_progress.json``, or None if absent/unreadable."""
    return load_json_dict(Path(output_base) / job_id / "docking_results" / "docking_progress.json")


def currently_docking(progress: dict, now: datetime) -> list[tuple[str, Optional[int]]]:
    """``(ligand, seconds running or None)`` for each ligand in progress."""
    docking = []
    for entry in progress.get("in_progress") or []:
        ligand = entry.get("ligand")
        if not ligand:
            continue
        elapsed = None
        started_at = entry.get("started_at")
        if started_at:
            try:
                elapsed = int((now - datetime.fromisoformat(started_at)).total_seconds())
            except ValueError:
                pass
        docking.append((ligand, elapsed))
    return docking


def read_job_log(output_base: Path, job_id: str) -> Optional[str]:
    """The session's ``job.log``, read fresh (it is appended to while a job
    runs), or None if there is none yet."""
    log_path = Path(output_base) / job_id / "job.log"
    if not log_path.exists():
        return None
    return log_path.read_text(encoding="utf-8", errors="replace")


def split_sdf_records(sdf_text: str) -> list[str]:
    """Split a multi-record SDF (one $$$$-terminated block per pose) into a
    list of individually valid single-record SDF texts, in file order."""
    blocks = re.split(r"\r?\n\$\$\$\$\r?\n?", sdf_text)
    return [block + "\n$$$$\n" for block in blocks if block.strip()]


def dock_results_by_ligand(result: dict) -> dict[str, dict]:
    """The dock step's per-ligand entries, by ligand id."""
    return {
        r["ligand_id"]: r
        for r in result.get("results", {}).get("dock", {}).get("results", [])
        if r.get("ligand_id")
    }


def pose_records(result: dict, output_base: Path, read_bytes: ReadBytes = _read_path) -> dict[str, list[str]]:
    """Each ligand's docked poses as single-record SDF texts, from the
    visualize step's converted SDF files, in that step's order. Ligands
    whose SDF is missing or empty are left out."""
    records_by_ligand: dict[str, list[str]] = {}
    for entry in result.get("results", {}).get("visualize", {}).get("converted", []):
        ligand_id = entry.get("ligand_id")
        raw_sdf_path = entry.get("sdf_path")
        if not ligand_id or not raw_sdf_path:
            continue
        sdf_path = from_container_path(raw_sdf_path, output_base)
        if not sdf_path.exists():
            continue
        records = split_sdf_records(read_bytes(sdf_path).decode("utf-8", errors="replace"))
        if records:
            records_by_ligand[ligand_id] = records
    return records_by_ligand


def viewer_ligand(records: list[str], dock_entry: dict, selected: int) -> dict:
    """The viewer's ``{"sdf", "affinity"}`` for pose number *selected*
    (1-based, clamped to the poses there are). The affinity is that mode's,
    else the ligand's best."""
    pose_index = min(selected - 1, len(records) - 1)
    modes = dock_entry.get("modes") or []
    affinity = (
        modes[pose_index].get("affinity")
        if pose_index < len(modes)
        else dock_entry.get("best_affinity")
    )
    return {"sdf": records[pose_index], "affinity": affinity}


def pose_label(modes: list[dict], i: int) -> str:
    """A pose picker option: "Mode i (-7.20 kcal/mol)", or "Mode i"."""
    if i - 1 < len(modes) and modes[i - 1].get("affinity") is not None:
        return f"Mode {i} ({modes[i - 1]['affinity']:.2f} kcal/mol)"
    return f"Mode {i}"


def pose_download_paths(result: dict, output_base: Path, fmt: str) -> list[tuple[str, Path]]:
    """``(ligand, file)`` for each docked ligand's pose file in *fmt*, if on disk.

    PDBQT is the docking engine's native output (no conversion, always
    available if docking succeeded for that ligand); PDB/SDF/MOL2 are the
    whole multi-pose file (all modes) written by the visualize step's
    convert_docked_poses, in whichever formats it was asked to produce.
    """
    converted = {
        entry["ligand_id"]: entry
        for entry in result.get("results", {}).get("visualize", {}).get("converted", [])
        if entry.get("ligand_id")
    }
    paths = []
    for ligand_id, dock_entry in dock_results_by_ligand(result).items():
        if fmt == "pdbqt":
            raw_path = dock_entry.get("output_path")
        else:
            raw_path = converted.get(ligand_id, {}).get(f"{fmt}_path")
        if not raw_path:
            continue
        path = from_container_path(raw_path, output_base)
        if path.exists():
            paths.append((ligand_id, path))
    return paths


def other_download_files(output_base: Path, job_id: str) -> list[Path]:
    """The job's ``.pml`` / ``.json`` / ``.png`` files, sorted, outside
    ``saved_results/``."""
    job_dir = Path(output_base) / job_id
    # Saved bundles are copies of the files already listed here -- pruned
    # from the walk rather than filtered after it, since each bundle can
    # hold a full copy of a large batch's docking_results/.
    files = []
    for root, dirnames, filenames in os.walk(job_dir):
        if Path(root) == job_dir and SAVED_RESULTS_DIRNAME in dirnames:
            dirnames.remove(SAVED_RESULTS_DIRNAME)
        files.extend(Path(root) / name for name in filenames)
    return [f for f in sorted(files) if f.suffix in OTHER_DOWNLOAD_SUFFIXES]


# ── One result's reads, at once (Stage 16.6) ──────────────────────────────────

@dataclass
class ResultsData:
    """Everything a completed result's view reads from disk."""

    # The visualize step's receptor, or None: no receptor in the result
    # (receptor_missing False) or none on disk (True).
    receptor_pdb: Optional[str] = None
    receptor_missing: bool = False
    pose_records: dict[str, list[str]] = field(default_factory=dict)
    # Per POSE_DOWNLOAD_FORMATS entry: (ligand, file) as pose_download_paths.
    pose_downloads: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)
    other_files: list[Path] = field(default_factory=list)


def read_results_data(output_base: Path, job_id: str, result: dict, read_bytes: ReadBytes = _read_path) -> ResultsData:
    """Read what a completed *result* shows: the receptor, each ligand's
    poses and the download lists. Slow on a large batch, so a GUI with an
    event loop runs it off the loop, once per result."""
    data = ResultsData()
    raw_receptor = result.get("results", {}).get("visualize", {}).get("receptor_pdb")
    if raw_receptor:
        receptor_path = from_container_path(raw_receptor, output_base)
        if receptor_path.exists():
            data.receptor_pdb = read_bytes(receptor_path).decode("utf-8", errors="replace")
        else:
            data.receptor_missing = True
    data.pose_records = pose_records(result, output_base, read_bytes)
    data.pose_downloads = {fmt: pose_download_paths(result, output_base, fmt) for fmt in POSE_DOWNLOAD_FORMATS}
    if (Path(output_base) / job_id).exists():
        data.other_files = other_download_files(output_base, job_id)
    return data


def job_log_if_changed(
    output_base: Path, job_id: str, last_stat: Optional[tuple[int, int]]
) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """``(stat, text)`` of the session's ``job.log``, where *stat* is its
    ``(mtime_ns, size)``. *text* is read only when *stat* differs from
    *last_stat*; otherwise it is None (unchanged). A missing log gives
    ``(None, None)``."""
    log_path = Path(output_base) / job_id / "job.log"
    try:
        st = log_path.stat()
    except FileNotFoundError:
        return None, None
    stat = (st.st_mtime_ns, st.st_size)
    if stat == last_stat:
        return stat, None
    return stat, log_path.read_text(encoding="utf-8", errors="replace")


def job_log_tail(text: str) -> str:
    """The Session Log viewer's text: the log's last JOB_LOG_TAIL_LINES lines."""
    return "\n".join(text.splitlines()[-JOB_LOG_TAIL_LINES:])


# ── The download route's path checks (Stage 16.6, finding 3) ─────────────────

DOWNLOAD_ROUTE = "/files"
_JOB_ID = re.compile(r"job_[A-Za-z0-9_]+")


def resolve_job_file(output_base: Path, job_id: str, rel_path: str) -> Optional[Path]:
    """The file *rel_path* inside session *job_id*'s directory, or None.

    The download route's security boundary: None for anything but a
    regular file inside ``<output_base>/<job_id>`` -- a ``..`` or absolute
    path, a symlink pointing out, a directory, a missing file, an id that
    is not a plain ``job_...`` name -- and for ``saved_results/``, whose
    bundles are copies of files listed already.
    """
    if not _JOB_ID.fullmatch(job_id or ""):
        return None
    job_dir = (Path(output_base) / job_id).resolve()
    try:
        path = (job_dir / rel_path).resolve()
    except (OSError, RuntimeError, ValueError):  # e.g. a symlink loop, a NUL byte
        return None
    if path == job_dir or not path.is_relative_to(job_dir):
        return None
    if path.relative_to(job_dir).parts[0] == SAVED_RESULTS_DIRNAME:
        return None
    return path if path.is_file() else None


def job_file_url(output_base: Path, job_id: str, path: Path) -> Optional[str]:
    """The download route's URL for *path*, a file in session *job_id*'s
    directory, or None if it lies outside it."""
    try:
        rel = Path(path).relative_to(Path(output_base) / job_id)
    except ValueError:
        return None
    return f"{DOWNLOAD_ROUTE}/{quote(job_id)}/{quote(PurePosixPath(*rel.parts).as_posix())}"
