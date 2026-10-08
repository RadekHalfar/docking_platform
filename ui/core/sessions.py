"""Session history actions: Load, rename, delete, revision history and
undo (Phase 16, Stage 16.1).

Moved unchanged in behaviour from ``ui/app.py``. Listing sessions is
``ui.job_history``'s job; caching that listing is each GUI's own concern.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from services.llm_orchestrator.workflow_definition import CORE_PARAMS, WORKFLOW_STEPS
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core.form import clear_box_picker_state, load_session_params, start_new_session
from ui.core.run_control import active_bridge_job_id
from ui.job_history import escape_markdown, rename_job


def load_workflow_results(output_base: Path, job_id: str) -> Optional[dict]:
    """Read workflow_results.json straight from disk for a job.

    Used to view jobs loaded from history (or run in a previous session /
    via the CLI), which have no in-memory OrchestratorBridge to poll.
    """
    results_file = Path(output_base) / job_id / "workflow_results.json"
    if not results_file.exists():
        return None
    try:
        return json.loads(results_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_params_into_form(state, params: dict) -> None:
    """Make *params* the form's full parameter state: ``state.params``, every
    form field (load_session_params) and the chat parser. Shared by a Load
    and a revision restore. In Streamlit it must run before the parameter
    widgets render (sidebar/chat code, or an on_click callback).
    """
    # A receptor loaded for box picking belongs to the previous protein; kept
    # across a Load or restore that changes the protein, a geometry chat
    # command would compute from the wrong one (Phase 12 R1's stale-receptor
    # fix only covered New Session and a fresh job -- Review Stage R1,
    # Phase 13).
    protein_keys = ("protein_id", "protein_file")
    if [state.params.get(k) for k in protein_keys] != [params.get(k) for k in protein_keys]:
        clear_box_picker_state(state)
    load_session_params(state, params)
    # Seed parser.parsed_workflow so a chat follow-up sent right after this
    # merges onto these parameters instead of being treated as a brand-new
    # workflow (see the parse_followup_with_llm use in the chat handler).
    state.parser.validate_workflow({"services": list(WORKFLOW_STEPS), "parameters": params})


def resume_session(state, job: dict, output_base: Path) -> None:
    """"Load": show a session's results *and* resume it as the active
    interactive session, so redocking can continue immediately instead of
    starting a brand-new job on the next "Run Docking".

    *job* is a ``ui.job_history`` listing entry. Reconstructing a
    WorkflowStateManager against an existing job_dir is safe -- it only
    creates the manifest file if missing, never overwrites existing
    checkpoints (see workflow_state_manager.py). The caller must make sure
    no bridge is running (:func:`active_bridge_job_id`).
    """
    state.viewed_job_id = job["job_id"]
    state_manager = WorkflowStateManager(Path(job["job_dir"]))
    # Fill the form from the job's saved parameters. A draft (saved via
    # "Save Session" without docking) takes priority over the last real
    # checkpoint since it's strictly newer.
    loaded_params = state_manager.load_draft_parameters() or state_manager.load_latest_parameters()
    if loaded_params:
        load_params_into_form(state, loaded_params)
    last_results = load_workflow_results(output_base, job["job_id"])
    session = state.interactive_session
    session.activate(job["job_id"], state_manager, last_results=last_results)
    # activate() starts `pending` empty, but the latest parameters may never
    # have run (a restored revision, a cancelled redock plan) -- the next
    # redock would then say "nothing to redock" while Save says "Run
    # Docking first" (Review Stage R1, Phase 13). The per-key detail isn't
    # on disk, so mark everything those parameters affect, as undo does.
    if not state_manager.parameters_ran():
        session.pending_changed_params |= CORE_PARAMS
    state.job_id = job["job_id"]
    state.job_name = job.get("job_name", "")
    # Keeps process_docking_bridge()'s completion sync from overwriting
    # session.last_results with a previous bridge's result (it only runs
    # while job_done is False, i.e. for a run it's actively tracking).
    state.job_done = True
    # A redock staged for the *previous* active session is no longer valid
    # once the active session switches to a different job_id -- discard it
    # rather than risk it being confirmed against this new job later
    # (confirm_redock also checks job_id as a second guard).
    state.pending_rerun = None


def rename_session(state, job: dict, new_name: str) -> Optional[str]:
    """Rename a session's display label; returns an error message, or None."""
    try:
        rename_job(job["job_dir"], new_name)
    except Exception as exc:
        return f"Rename failed: {exc}"
    if state.viewed_job_id == job["job_id"]:
        state.job_name = new_name.strip()[:60]
    return None


def delete_session(state, job: dict) -> Optional[str]:
    """Delete a session's directory; returns an error message, or None.
    Deleting the active session starts a new one. The caller must not
    offer this for the job this client is running (:func:`active_bridge_job_id`)."""
    try:
        shutil.rmtree(job["job_dir"])
    except OSError as exc:
        return f"Delete failed: {exc}"
    if state.viewed_job_id == job["job_id"]:
        state.viewed_job_id = None
    if state.job_id == job["job_id"]:
        start_new_session(state)
    return None


def undo_to_revision(state, revision: int) -> str:
    """Restore the active session's parameters to *revision* (``undo <rev>``)
    and return the reply to show.

    Shared by the revision history panel's button and the chat command.
    In Streamlit it must run before the parameter widgets render, since it
    rewrites their keys.
    """
    session = state.interactive_session
    if not session.has_active_job():
        return "No active session — run or load one first."
    if active_bridge_job_id(state) is not None:
        # The orchestrator re-reads the latest parameters before every
        # step, so a restore now would change the running job mid-flight.
        return "A session is running — Stop it before restoring a revision."

    try:
        has_parameters = session.state_manager.load_checkpoint(revision)["parameters"] is not None
    except ValueError as exc:
        return f"Cannot restore revision {revision}: {exc}"
    if not has_parameters:
        # Restoring only a runtime-state checkpoint would roll back
        # workflow_state.json under results it no longer matches, with
        # nothing marked to rerun -- the revision history panel only offers
        # parameter revisions, so chat doesn't either (R1, Phase 13).
        return (
            f"Revision {revision} has no parameter checkpoint — only revisions marked "
            "[params] in `show history` can be restored."
        )

    restored = session.state_manager.restore_revision(revision)["parameters"]

    # A draft would otherwise win over the restored revision on the next Load.
    session.state_manager.clear_draft_parameters()
    state.pending_rerun = None
    load_params_into_form(state, restored)
    # The results on disk come from the newer parameters -- the next redock
    # must rerun everything those affect, even though the form now matches
    # the checkpoint exactly (mirrors the CLI's undo).
    session.pending_changed_params |= CORE_PARAMS
    return (
        f"Restored revision {revision}. The results panel still shows the last run — "
        "click **Run Docking** to rerun with the restored parameters."
    )


def format_history_markdown(revisions: list[dict]) -> str:
    """A job's revision history (``WorkflowStateManager.list_revisions()``)
    as a markdown table -- the GUI's equivalent of the CLI's ``show history``."""
    if not revisions:
        return "No revisions recorded yet."

    lines = ["| Rev | Time (UTC) | Event | Details |", "|---|---|---|---|"]
    for r in revisions:
        details = ", ".join(f"{k}={v}" for k, v in r["details"].items())
        flags = [name for name, present in (("params", r["has_parameters"]), ("state", r["has_state"])) if present]
        if flags:
            details = f"{details} [{', '.join(flags)}]" if details else f"[{', '.join(flags)}]"
        timestamp = r["timestamp"][:19].replace("T", " ")
        event, details = (escape_markdown(s).replace("|", "\\|") for s in (r["event_type"], details))
        lines.append(f"| {r['revision']} | {timestamp} | {event} | {details} |")
    return "\n".join(lines)


def history_chat_reply(state, intent: dict) -> str:
    """The reply to a deterministic ``show history`` / ``undo [N]`` chat command."""
    session = state.interactive_session
    if intent["intent"] == "show_history":
        if not session.has_active_job():
            return "No active session — no history yet."
        current = session.state_manager.get_parameters_revision()
        return (
            format_history_markdown(session.state_manager.list_revisions())
            + f"\n\nParameters are currently at revision **{current}**. "
            "Type `undo <revision>` to restore one."
        )
    if intent.get("name") is None:
        return "Which revision? e.g. `undo 3` — type `show history` to list them."
    return undo_to_revision(state, int(intent["name"]))
