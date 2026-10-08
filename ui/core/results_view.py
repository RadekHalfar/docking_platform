"""What the results panel shows, as data (Phase 16, Stage 16.2).

Moved unchanged in behaviour from ``ui/app.py``'s render functions; the
widgets stay in each GUI. Paths in a workflow result are the MCP
services' own (``/app/orchestrator_output/...``) and are mapped onto
*output_base* with ``common.path_utils.from_container_path``.

Helpers that read result files take *read_bytes*, so a GUI can pass a
cached reader (Streamlit re-reads them on every rerun otherwise).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from common.docking_algorithm import docking_algorithm_label, docking_algorithm_of
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
    path = Path(output_base) / job_id / "docking_results" / "docking_progress.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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
