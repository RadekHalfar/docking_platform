"""Append-only, human-readable session log for a job directory.

Complements the existing "latest" + revisioned-checkpoint files
(:mod:`services.llm_orchestrator.workflow_state_manager`) with a single
plain-text timeline of a session: parameter changes, redocks, step
progress, and errors, in the order they happened -- something no other
file in ``job_<id>/`` captures across more than one ``execute_workflow()``
call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from common.file_io import FileLock

_LOG_FILENAME = "job.log"
_LOCK_FILENAME = ".job_log.lock"


def append_job_log(job_dir: "str | Path", level: str, message: str) -> None:
    """Append one timestamped line to ``job_dir/job.log``.

    Guarded by a :class:`~common.file_io.FileLock` on a ``.job_log.lock``
    file so the orchestrator process and ``WorkflowStateManager`` can't
    interleave partial lines when both touch the same job_dir mid-run.
    """
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} [{level}] {message}\n"

    with FileLock(job_dir / _LOCK_FILENAME):
        with open(job_dir / _LOG_FILENAME, "a", encoding="utf-8") as f:
            f.write(line)
