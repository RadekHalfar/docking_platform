"""List completed/failed docking jobs for the sidebar session-history / comparison picker.

No Streamlit dependency — kept pure so it can be unit tested directly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from common.file_io import FileLock, atomic_write_json

_MD_SPECIAL_RE = re.compile(r'([\\`*_\[\]#])')


# A session name's length limit: every name field, and what is saved.
JOB_NAME_MAX_CHARS = 60


def escape_markdown(text: str) -> str:
    """Escape characters Streamlit's markdown renderer treats specially.

    Used for user-supplied job names interpolated into markdown-rendered
    labels/captions, so e.g. a name containing "**" can't visually break
    the surrounding bold/formatting.
    """
    return _MD_SPECIAL_RE.sub(r'\\\1', text)


def _completed_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    dock = data.get("results", {}).get("dock", {})
    return {"ligand_count": len(dock.get("results", []))}


def _failed_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    errors = data.get("errors") or []
    return {"first_error": errors[0] if errors else "Unknown error"}


# Each listing: the statuses it keeps, and the fields it adds to the common
# ones (job_id/job_dir/job_name/protein_id).
_COMPLETED = ({"completed"}, _completed_fields)
# A cancelled run (stopped via the GUI's "Stop" button) is listed alongside
# genuine failures -- both are "not completed", and share the same
# Load/Rename/Delete affordances.
_FAILED = ({"failed", "cancelled"}, _failed_fields)


def _scan_jobs(
    output_base: str,
    listings: Sequence[Tuple[set, Callable[[Dict[str, Any]], Dict[str, Any]]]],
) -> List[List[Dict[str, Any]]]:
    """Shared scan: parse every workflow_results.json under output_base
    once, and sort each job into the first of *listings* whose statuses
    hold its status. Returns one list per listing, newest first (job IDs
    are timestamp-prefixed, e.g. job_20260701_...).

    One pass for all listings: parsing every session's file is the slow
    part (4-6 s for ~170 sessions on a Docker Desktop bind mount, Review
    Stage R3), so a caller wanting several listings shouldn't scan twice.
    """
    found: List[List[Dict[str, Any]]] = [[] for _ in listings]
    base = Path(output_base)
    if not base.exists():
        return found

    for results_file in base.glob("*/workflow_results.json"):
        try:
            data = json.loads(results_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue

        for jobs, (statuses, extra_fields) in zip(found, listings):
            if data.get("status") not in statuses:
                continue
            get_input_files = data.get("results", {}).get("get_input_files", {})
            protein_path = get_input_files.get("protein_path")
            protein_id = Path(protein_path).stem if protein_path else "?"
            job = {
                "job_id": results_file.parent.name,
                "job_dir": str(results_file.parent),
                "job_name": data.get("job_name", ""),
                "protein_id": protein_id,
            }
            job.update(extra_fields(data))
            jobs.append(job)
            break

    return [sorted(jobs, key=lambda j: j["job_id"], reverse=True) for jobs in found]


def list_completed_jobs(output_base: str) -> List[Dict[str, Any]]:
    """Scan output_base for completed jobs.

    Returns a list of {job_id, job_dir, job_name, protein_id, ligand_count}
    dicts, sorted newest first.
    """
    return _scan_jobs(output_base, [_COMPLETED])[0]


def list_failed_jobs(output_base: str) -> List[Dict[str, Any]]:
    """Scan output_base for failed or user-cancelled jobs.

    Returns a list of {job_id, job_dir, job_name, protein_id, first_error}
    dicts, sorted newest first.
    """
    return _scan_jobs(output_base, [_FAILED])[0]


def list_completed_and_failed_jobs(output_base: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """:func:`list_completed_jobs` and :func:`list_failed_jobs` in one scan."""
    completed, failed = _scan_jobs(output_base, [_COMPLETED, _FAILED])
    return completed, failed


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
