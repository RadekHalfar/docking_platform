"""Run, redock, stop and save-session actions (Phase 16, Stage 16.1).

Moved from ``ui/app.py``. Replies go into ``state.messages``; what else
the page must do comes back as an :class:`~ui.core.state.Outcome`
(``refresh``: redraw now; ``error``: an inline error next to the control).

The bridge class is passed in (*bridge_factory*), never imported here: the
GUI tests swap it for a fake, and each GUI owns which class it runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from common.utils import generate_job_id
from services.llm_orchestrator.interactive_session import compute_changed_params
from services.llm_orchestrator.rerun_planner import compute_rerun_steps, missing_required_params
from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS, with_default_parameters
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core.form import clear_box_picker_state, sync_form_into_params
from ui.core.state import Outcome, reply

BridgeFactory = Callable[[], Any]

# DEFERRED (Review Stage R1, docs/reviews/stage_16_r1.md, deferred #5):
# output_base and bridge_factory are passed separately into every action
# here and in box_picker/chat/save_results/sessions. Fine with one GUI
# calling a few of them. Trigger: if wiring NiceGUI (Stages 16.3-16.7) means
# threading the same two or three values through many call sites, bundle
# them in one per-app `GuiDeps(output_base, bridge_factory, ...)` built at
# startup and pass that instead, in one change across ui/core.


def active_bridge_job_id(state) -> Optional[str]:
    """job_id this client's own docking bridge is actively running, if any.

    Only this client's own bridge is visible here -- a different
    tab/process running the same job_id is not detectable, and that
    residual gap is documented rather than silently assumed solved.

    Used to guard every action that would detach the state from the job a
    live bridge is writing into (New Session, Reset, Load, Delete, restore,
    save) -- detaching without checking this first would orphan the running
    background thread (it keeps writing to its job_dir with nothing left in
    the state able to observe or stop it) and, for Load/Delete
    specifically, desyncs ``job_id`` from the bridge's real target, which
    defeats the very running-job guard those actions rely on (see
    docs/reviews/stage_11_2_11_3_r2.md).
    """
    bridge = state.bridge
    if bridge is not None and bridge.is_running():
        return state.job_id
    return None


def any_bridge_running(state) -> bool:
    """Whether the docking bridge or the box picker's receptor prep is running."""
    return any(b is not None and b.is_running() for b in (state.bridge, state.box_picker_bridge))


def run_docking(state, output_base: Path, bridge_factory: BridgeFactory) -> Outcome:
    """"Run Docking": start a new job, or -- with a session active -- stage
    a redock plan for confirmation (:func:`plan_redock`)."""
    params = sync_form_into_params(state)

    session = state.interactive_session
    if session.has_active_job():
        return plan_redock(state, params)

    # A brand-new job is about to start -- any box-picker receptor loaded
    # for a previous (unrelated) protein/session no longer applies. Without
    # this, a geometry chat command would keep preferring that stale path
    # over this new job's own prepare_protein result once it completes,
    # silently computing geometry from the wrong protein (Review Stage R1,
    # Phase 12).
    clear_box_picker_state(state)

    # Always include visualization — it runs after docking and produces the PNG.
    services = list(WORKFLOW_STEPS)
    try:
        validated = state.parser.validate_workflow({"services": services, "parameters": params})
    except Exception as exc:
        return Outcome(error=f"Validation error: {exc}")

    missing = validated.get("missing_required", [])
    if missing:
        reply(state, f"Cannot start — required parameters missing: **{', '.join(missing)}**.")
        return Outcome()

    job_id = generate_job_id()
    job_name = state.form.get("param_job_name", "").strip()[:60]
    state.job_id = job_id
    state.job_name = job_name
    state.viewed_job_id = job_id
    state.job_done = False
    state.run_log = []

    state_manager = WorkflowStateManager(Path(output_base) / job_id)
    state_manager.save_parameters_checkpoint(validated["parameters"], source="initial_parse")
    state_manager.clear_draft_parameters()
    session.activate(job_id, state_manager)

    bridge = bridge_factory()
    bridge.start_workflow(
        validated, job_id, str(output_base), job_name=job_name, state_manager=state_manager
    )
    state.bridge = bridge

    reply(state, f"Starting docking session `{job_id}`...")
    return Outcome(refresh=True)


def plan_redock(state, params: dict) -> Outcome:
    """Diff *params* against the active session's last checkpoint and, if a
    rerun is warranted, stage a plan (``state.pending_rerun``) for
    confirmation.

    Mirrors the CLI's ``_execute_partial_rerun`` (describe_rerun_plan +
    ``[Y/n]`` prompt) via a two-step "Run Docking" → "Confirm redock" pair,
    since a GUI has no blocking ``input()``.
    """
    session = state.interactive_session
    # The form doesn't carry every parameter (energy_range, num_modes,
    # population, ...): with the defaults filled in, params is what the
    # checkpoint below holds and what the redock runs with.
    params = with_default_parameters(params)
    old_params = session.state_manager.load_latest_parameters() or {}
    changed = compute_changed_params(old_params, params)
    # pending_changed_params holds changes already checkpointed but not yet
    # run to completion: a restored revision (all CORE_PARAMS), or an
    # earlier redock that was cancelled / failed. The form matches the
    # checkpoint in those cases, so the diff alone would miss them.
    rerun_keys = changed | session.pending_changed_params

    # Steps the last run never got to (stopped or failed part-way) must run
    # even with no parameter change, and must stay plannable: intersecting
    # with the executed steps alone dropped dock/visualize for good after a
    # stopped first run (Review Stage R1, Phase 13).
    last = session.last_results or {}
    executed = set(last.get("services_executed", []))
    unfinished = set() if last.get("status") == "completed" else set(WORKFLOW_STEPS) - executed
    if not rerun_keys and not unfinished:
        reply(state, "No parameter changes detected — nothing to redock.")
        return Outcome()

    if changed:
        session.state_manager.save_parameters_checkpoint(params, source="interactive_update")
        session.state_manager.clear_draft_parameters()

    planned = set(compute_rerun_steps(rerun_keys, original_services=executed | unfinished)) | unfinished
    steps = [s for s in WORKFLOW_STEPS if s in planned]
    if not steps:
        reply(state, "Parameters updated, but no steps need to be re-executed.")
        # Not for the reply: the checkpoint above is a new revision, which
        # the revision history panel must show.
        return Outcome(refresh=True)

    # The checkpoint above now runs ahead of the results on disk until a
    # run completes (process_docking_bridge() clears this) -- Cancel or the
    # error return below must not lose that.
    session.pending_changed_params |= changed

    # params is what the checkpoint holds -- reuse it instead of a
    # redundant round trip back through the checkpoint file.
    missing = missing_required_params(steps, params)
    if missing:
        return Outcome(
            error="Cannot redock — required parameters are missing: " + ", ".join(sorted(set(missing)))
        )

    state.pending_rerun = {"job_id": session.job_id, "steps": steps, "params": params}
    return Outcome(refresh=True)


def confirm_redock(state, output_base: Path, bridge_factory: BridgeFactory) -> Outcome:
    """"Confirm redock": run the staged plan."""
    pending = state.pending_rerun
    state.pending_rerun = None
    if not pending:
        return Outcome()

    session = state.interactive_session
    if pending.get("job_id") != session.job_id:
        # The active session changed (e.g. a Load) after this plan was
        # staged but before it was confirmed -- the staged steps/params
        # were computed for a different job_id and must not be applied here.
        return Outcome(
            refresh=True,
            error="Redock plan was for a different session and has been discarded — please try again.",
        )

    rerun_workflow = {"services": pending["steps"], "parameters": pending["params"]}

    state.job_done = False
    state.run_log = []
    state.viewed_job_id = session.job_id

    bridge = bridge_factory()
    bridge.start_workflow(
        rerun_workflow,
        session.job_id,
        str(output_base),
        job_name=state.job_name,
        state_manager=session.state_manager,
    )
    state.bridge = bridge

    reply(state, f"Redocking `{session.job_id}` — steps: {', '.join(pending['steps'])}...")
    return Outcome(refresh=True)


def cancel_redock(state) -> Outcome:
    """"Cancel" on a staged redock plan. Its checkpoint stays, and
    ``pending_changed_params`` keeps it plannable next time."""
    state.pending_rerun = None
    return Outcome(refresh=True)


def stop_run(state) -> Outcome:
    """"Stop": ask the running docking bridge to stop (see
    ``OrchestratorBridge.request_stop``)."""
    if state.bridge is not None and state.bridge.is_running():
        state.bridge.request_stop()
    return Outcome(refresh=True)


def save_session(state) -> Outcome:
    """"Save Session": persist the form's parameter edits as a draft,
    without running/redocking anything. Lets edits (e.g. a box-size tweak)
    survive switching to a different session and back.

    With nothing changed against the checkpoint, any earlier draft is
    dropped: Load prefers a draft, so a stale one would bring back an edit
    the form has since been set back from."""
    session = state.interactive_session
    if not session.has_active_job():
        return Outcome()
    params = sync_form_into_params(state)
    checkpointed = session.state_manager.load_latest_parameters() or {}
    if not compute_changed_params(checkpointed, params):
        session.state_manager.clear_draft_parameters()
        reply(state, "No parameter changes to save.")
    else:
        session.state_manager.save_draft_parameters(params)
        reply(state, "Parameters saved. Click **Run Docking** to apply them.")
    return Outcome()


def process_docking_bridge(state) -> None:
    """Collect a running job's log lines into ``state.run_log`` and, once
    it has finished, record the outcome and post the reply -- exactly once
    (``state.job_done``). Called on every page update / poll tick."""
    bridge = state.bridge
    if not bridge:
        return

    # Drained once more after the job ends, or its last lines ("WORKFLOW
    # COMPLETED SUCCESSFULLY") are lost. is_running() is read *before*
    # draining: once the thread is seen finished, everything it printed is
    # already queued. The log is the run's own, not the last chat message's
    # (Review Stage R1, deferred #1): a chat sent mid-run stays as typed.
    running = bridge.is_running()
    state.run_log.extend(bridge.get_log_lines())
    if running:
        return

    if state.job_done:
        return
    result = bridge.get_result()
    error = bridge.get_error()
    # Sync the session's view of "what's been run" from the already-merged
    # result execute_workflow() returns, so a subsequent redock's
    # original_services/last_results reflect this run instead of going
    # stale -- reusing the on-disk merge execute_workflow() already does
    # rather than re-deriving it here (see docs/reviews/stage_11_1_r1.md
    # finding #5).
    session = state.interactive_session
    if result is not None and session.has_active_job() and session.job_id == state.job_id:
        session.last_results = result

    status = (result or {}).get("status")
    if status == "completed":
        # The checkpointed parameters have now actually been run. Only a
        # *completed* run clears them (stricter than the CLI): after a
        # stop/failure the results on disk still predate them, so the next
        # redock must still include them.
        if session.job_id == state.job_id:
            session.pending_changed_params = set()
        outcome_message = "Workflow completed successfully!"
    elif error:
        outcome_message = f"Workflow failed: {error}"
    elif status == "cancelled":
        outcome_message = "Session stopped."
    elif status == "failed":
        outcome_message = "Workflow failed: " + ("; ".join(result.get("errors", [])) or "unknown error")
    else:
        return
    state.job_done = True
    reply(state, outcome_message)
