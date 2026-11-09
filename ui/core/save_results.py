"""Save results as… -- copy the active session's results into a named
bundle.

Shared by the results
panel's controls and the chat's ``save results/docking/visualization as
<name>`` command. A name that already exists is not an error: it records
``state.save_conflict``, which the panel offers to resolve as Overwrite /
Save as ``<name>_HHMMSS``.

Copying a real session's results takes tens of seconds on a Docker Desktop
bind mount (34-37 s for 17 MB of docking results). So a
save is three steps, and a GUI with an event loop runs only the middle one
off it: :func:`plan_save` (checks, on the state), :func:`write_bundle` (the
copy; touches no GUI state), :func:`finish_save` (the reply and the
conflict, on the state).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from services.llm_orchestrator.interactive_session import (
    save_results_bundle,
    timestamped_bundle_name,
    validate_bundle_name,
)
from ui.core.run_control import active_bridge_job_id
from ui.core.sessions import load_workflow_results

SAVE_SCOPES = ("all", "docking", "visualization")
SCOPE_LABELS = {
    "all": "Docking + visualization",
    "docking": "Docking results only",
    "visualization": "Visualization only",
}


def save_block_reason(state, output_base: Path) -> Optional[str]:
    """Why the active session's results can't be saved right now, or None.

    Shared by the Save results panel (shown as a caption) and the chat
    ``save results as`` command (sent as the reply).
    """
    session = state.interactive_session
    if not session.has_active_job():
        return "No active session — run or load one first."
    if active_bridge_job_id(state) is not None:
        return "A session is running — wait for it to finish before saving results."
    if session.pending_changed_params:
        changed = ", ".join(sorted(session.pending_changed_params))
        return (
            f"Parameters changed since the last completed run ({changed}) — "
            "click **Run Docking** first, or restore a revision."
        )
    if not session.state_manager.parameters_ran():
        # The on-disk form of the check above: still true after a Load or a
        # restart, which reset `pending` (e.g. a restore never redocked).
        return (
            f"The parameters (revision {session.state_manager.get_parameters_revision()}) "
            "have not been run yet — the results shown come from earlier parameters. "
            "Click **Run Docking** first, or restore the revision the results came from."
        )
    result = load_workflow_results(output_base, session.job_id)
    if not result or result.get("status") != "completed":
        return "The session's last run did not complete — there are no results to save."
    return None


@dataclass(frozen=True)
class SaveJob:
    """A save :func:`plan_save` cleared: what :func:`write_bundle` copies."""

    job_id: str
    state_manager: Any  # the session's WorkflowStateManager
    name: str
    scope: str
    overwrite: bool


def plan_save(
    state, output_base: Path, name: str, scope: str, *, overwrite: bool = False
) -> tuple[Optional[str], Optional[SaveJob]]:
    """Check a save of the active session's results as bundle *name*:
    ``(reply, None)`` if it can't be done, else ``(None, job)``."""
    reason = save_block_reason(state, output_base)
    if reason:
        return f"Cannot save: {reason}", None
    err = validate_bundle_name(name)
    if err:
        return f"Invalid bundle name: {err}", None
    session = state.interactive_session
    return None, SaveJob(session.job_id, session.state_manager, name, scope, overwrite)


def write_bundle(job: SaveJob) -> dict:
    """Copy the results into the bundle (slow; reads and writes only the
    job dir, so it may run on another thread)."""
    state_manager = job.state_manager
    return save_results_bundle(
        state_manager.job_dir,
        job.name,
        job.scope,
        state_manager.get_current_revision(),
        state_manager.load_latest_parameters() or {},
        overwrite=job.overwrite,
    )


def finish_save(state, job: SaveJob, result: dict) -> str:
    """Record a :func:`write_bundle` result and return the reply to show.

    A name that already exists records ``state.save_conflict`` (see the
    module docstring); a successful save clears it.
    """
    job_dir = job.state_manager.job_dir
    if result["error"] == "exists":
        suggested = timestamped_bundle_name(job_dir, job.name)
        state.save_conflict = {
            "job_id": job.job_id, "name": job.name, "scope": job.scope, "suggested": suggested,
        }
        return (
            f"A bundle named `{job.name}` already exists — choose **Overwrite** or "
            f"**Save as {suggested}** under *Save results as…* in the results panel."
        )
    if not result["success"]:
        return f"Save failed: {result['error']}"

    state.save_conflict = None
    final_name = Path(result["bundle_path"]).name
    lines = [f"Saved bundle `{final_name}` (scope: {job.scope}) to `{result['bundle_path']}`."]
    if result["artifacts"]:
        lines.append(f"Artifacts: {', '.join(result['artifacts'])}.")
    else:
        lines.append("Warning: no artifacts were available to copy into the bundle.")
    return "\n\n".join(lines)


def plan_chat_save(state, output_base: Path, intent: dict) -> tuple[Optional[str], Optional[SaveJob]]:
    """:func:`plan_save` for a deterministic ``save results/docking/visualization as <name>``."""
    if not intent.get("name"):
        return "Give the bundle a name, e.g. `save results as run1`.", None
    return plan_save(state, output_base, intent["name"], intent.get("scope") or "all")


def plan_conflict_save(
    state, output_base: Path, *, overwrite: bool
) -> tuple[Optional[str], Optional[SaveJob]]:
    """:func:`plan_save` for the conflict's Overwrite (*overwrite*) or
    "Save as <suggested>" button; ``(None, None)`` with no conflict."""
    conflict = state.save_conflict
    if not conflict:
        return None, None
    name = conflict["name"] if overwrite else conflict["suggested"]
    return plan_save(state, output_base, name, conflict["scope"], overwrite=overwrite)


def cancel_save_conflict(state) -> None:
    """The conflict's Cancel button."""
    state.save_conflict = None
