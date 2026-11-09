"""Cross-container "stop docking now" signal.

The dock step is one blocking ``run_docking`` MCP call to ``moldina-runner``
that loops over every ligand, so the orchestrator's between-steps
``cancel_event`` can't interrupt it. The signal instead travels over the
shared ``orchestrator_output`` volume, as a flag file in the job's
``docking_results/`` directory (the ``output_dir`` the call already
receives):

- the orchestrator clears any stale flag right before calling
  ``run_docking`` (:func:`clear_docking_stop`);
- a stop request writes it (:func:`request_docking_stop` -- from the GUI's
  ``OrchestratorBridge.request_stop()``);
- ``moldina-runner`` polls it (:func:`docking_stop_requested`) while
  docking: no further ligands start, and running Moldina processes are
  killed. It never deletes the flag.

Each container sees the volume at its own mount path, so every function
takes the docking directory as *that process's* local path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

STOP_FLAG_NAME = "STOP_REQUESTED"

PathLike = Union[str, Path]


def stop_flag_path(docking_dir: PathLike) -> Path:
    """Return the stop-flag path inside *docking_dir*."""
    return Path(docking_dir) / STOP_FLAG_NAME


def request_docking_stop(docking_dir: PathLike) -> bool:
    """Write the stop flag. Returns True if written.

    Only writes when *docking_dir* already exists: creating it here (as
    whichever user this process runs as) could leave a directory the
    non-root ``moldina-runner`` can't write its results into -- the
    orchestrator creates it with ``ensure_shared_directory()`` before the
    dock step. A stop requested before that point is caught by the
    orchestrator's own ``cancel_event`` check instead.
    """
    docking_dir = Path(docking_dir)
    if not docking_dir.is_dir():
        return False
    stop_flag_path(docking_dir).touch()
    return True


def clear_docking_stop(docking_dir: PathLike) -> None:
    """Remove a (possibly stale) stop flag; a no-op when there is none."""
    stop_flag_path(docking_dir).unlink(missing_ok=True)


def docking_stop_requested(docking_dir: PathLike) -> bool:
    """Return True if a stop has been requested for this docking run."""
    return stop_flag_path(docking_dir).exists()
