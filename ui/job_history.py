"""List completed/failed docking jobs for the Sessions tab and its comparison picker.

No GUI framework dependency -- kept pure so it can be unit tested directly.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.file_io import FileLock, atomic_write_json, load_json_dict

_MD_SPECIAL_RE = re.compile(r'([\\`*_\[\]#])')


# A session name's length limit: every name field, and what is saved.
JOB_NAME_MAX_CHARS = 60


def escape_markdown(text: str) -> str:
    """Escape characters the GUI's markdown renderer treats specially.

    Used for user-supplied job names interpolated into markdown-rendered
    labels/captions, so e.g. a name containing "**" can't visually break
    the surrounding bold/formatting.
    """
    return _MD_SPECIAL_RE.sub(r'\\\1', text)


# A cancelled run (stopped via the GUI's "Stop" button) is listed alongside
# genuine failures -- both are "not completed", and share the same
# Load/Rename/Delete affordances.
_FAILED_STATUSES = {"failed", "cancelled"}

# Parsed listing entries by results file, with the (mtime_ns, size) they
# were parsed at. Parsing every session's file is the slow part of a
# listing (4-6 s for ~190 sessions on a Docker Desktop bind mount), so a re-list re-reads only the files that changed. Shared
# by every client; the scans run on worker threads, hence the lock.
_entry_cache: Dict[str, Tuple[Tuple[int, int], Optional[Tuple[str, Dict[str, Any]]]]] = {}
_entry_cache_lock = threading.Lock()


def _listing_entry(results_file: Path) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``("completed" | "failed", job)`` for one session's results file, or
    None if it is neither (running, unreadable, not a dict)."""
    data = load_json_dict(results_file)
    if data is None:
        return None
    status = data.get("status")
    if status == "completed":
        kind = "completed"
        dock = data.get("results", {}).get("dock", {})
        extra = {"ligand_count": len(dock.get("results", []))}
    elif status in _FAILED_STATUSES:
        kind = "failed"
        errors = data.get("errors") or []
        extra = {"first_error": errors[0] if errors else "Unknown error"}
    else:
        return None
    protein_path = data.get("results", {}).get("get_input_files", {}).get("protein_path")
    job = {
        "job_id": results_file.parent.name,
        "job_dir": str(results_file.parent),
        "job_name": data.get("job_name", ""),
        "protein_id": Path(protein_path).stem if protein_path else "?",
        **extra,
    }
    return kind, job


def list_completed_and_failed_jobs(output_base: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scan output_base for completed jobs and for failed or user-cancelled
    ones.

    Returns two lists, each sorted newest first (job IDs are
    timestamp-prefixed, e.g. job_20260701_...): completed jobs as
    {job_id, job_dir, job_name, protein_id, ligand_count} dicts, failed ones
    as {job_id, job_dir, job_name, protein_id, first_error} dicts. Each call
    returns fresh dicts; only files changed since the last scan are parsed.
    """
    completed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    base = Path(output_base)
    if not base.exists():
        return completed, failed

    seen: Dict[str, Tuple[Tuple[int, int], Optional[Tuple[str, Dict[str, Any]]]]] = {}
    for results_file in base.glob("*/workflow_results.json"):
        try:
            st = results_file.stat()
        except OSError:
            continue
        key, stamp = str(results_file), (st.st_mtime_ns, st.st_size)
        with _entry_cache_lock:
            cached = _entry_cache.get(key)
        entry = cached[1] if cached is not None and cached[0] == stamp else _listing_entry(results_file)
        seen[key] = (stamp, entry)
        if entry is not None:
            kind, job = entry
            (completed if kind == "completed" else failed).append(dict(job))

    with _entry_cache_lock:
        # Drop entries of this base's sessions that are gone (deleted).
        prefix = str(base)
        for key in [k for k in _entry_cache if k.startswith(prefix) and k not in seen]:
            del _entry_cache[key]
        _entry_cache.update(seen)

    def newest_first(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(jobs, key=lambda j: j["job_id"], reverse=True)

    return newest_first(completed), newest_first(failed)


def rename_job(job_dir: str, new_name: str) -> None:
    """Update the job_name field of a job's workflow_results.json in place.

    Locked and atomic to match the orchestrator's own read-merge-write of
    this same file (services/llm_orchestrator/orchestrator.py, same
    ``.workflow_results.lock`` path) -- without this, renaming a session
    while it's actively being redocked could race that write and clobber
    the redock's freshly-merged results with this function's stale read.
    """
    results_file = Path(job_dir) / "workflow_results.json"
    lock_file = Path(job_dir) / ".workflow_results.lock"
    with FileLock(lock_file):
        data = json.loads(results_file.read_text())
        data["job_name"] = new_name.strip()[:JOB_NAME_MAX_CHARS]
        atomic_write_json(results_file, data)
