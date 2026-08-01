"""
Job Retention / Cleanup

orchestrator_output/ has no automatic pruning anywhere in the codebase, so
job directories accumulate forever. This module only ever *finds* candidates
for deletion -- it never deletes anything on its own. The CLI wrapper
(`nlp_orchestrator.py cleanup-jobs`) is opt-in and defaults to a dry run;
actually removing files requires an explicit --yes.
"""
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# job_id format: job_YYYYMMDD_HHMMSS_<hex> (see common.utils.generate_job_id).
# Only directories matching this are ever considered -- other directories
# under orchestrator_output/ (e.g. _benchmark_*, uploads/) are deliberately
# left alone, since they aren't "jobs" this tool knows how to reason about.
_JOB_ID_TIMESTAMP_RE = re.compile(r"^job_(\d{8})_(\d{6})_")


def _job_timestamp(job_id: str) -> Optional[datetime]:
    """Parse the timestamp embedded in a job_id, or None if it doesn't match.

    Returned as a naive datetime in the local timezone, not UTC:
    common.utils.generate_job_id() builds this timestamp from
    datetime.now() (naive local time), so labeling it UTC here would skew
    every comparison by the host's UTC offset.
    """
    match = _JOB_ID_TIMESTAMP_RE.match(job_id)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def find_prunable_jobs(
    output_base: str,
    keep_last: Optional[int] = None,
    older_than_days: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return job directories eligible for cleanup, most-recently-excluded-first.

    A job only qualifies if it satisfies every filter that was actually
    passed in. At least one of keep_last/older_than_days is required --
    calling this with neither returns an empty list rather than treating
    "no filters" as "everything", so a caller can never accidentally nuke
    the whole directory by forgetting to pass a filter.
    """
    if keep_last is None and older_than_days is None:
        return []

    base = Path(output_base)
    if not base.exists():
        return []

    dated: List[Tuple[Path, datetime]] = []
    for job_dir in base.glob("job_*"):
        if not job_dir.is_dir():
            continue
        timestamp = _job_timestamp(job_dir.name)
        if timestamp is None:
            continue
        dated.append((job_dir, timestamp))

    dated.sort(key=lambda pair: pair[1], reverse=True)  # newest first

    candidates = dated[keep_last:] if keep_last is not None else dated

    if older_than_days is not None:
        # Naive local time, matching _job_timestamp() -- comparing against
        # datetime.now(timezone.utc) here would be comparing two different
        # clocks and skew the cutoff by the host's UTC offset.
        cutoff = datetime.now() - timedelta(days=older_than_days)
        candidates = [(job_dir, ts) for job_dir, ts in candidates if ts < cutoff]

    return [
        {"job_id": job_dir.name, "job_dir": str(job_dir), "timestamp": ts.isoformat()}
        for job_dir, ts in candidates
    ]


def delete_jobs(job_dirs: List[str]) -> Tuple[int, List[str]]:
    """Delete each job directory. Returns (number_deleted, error_messages).

    Best-effort: one failure doesn't stop the rest from being attempted.
    """
    deleted = 0
    errors: List[str] = []
    for job_dir in job_dirs:
        try:
            shutil.rmtree(job_dir)
            deleted += 1
        except OSError as exc:
            errors.append(f"{job_dir}: {exc}")
    return deleted, errors
