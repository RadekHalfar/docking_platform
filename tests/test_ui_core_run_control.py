"""Tests for ui/core/run_control.py (Stage 16.1) -- hermetic.

A plain GuiSession, a fake bridge that never starts a thread, and a
tmp_path OUTPUT_BASE. The ``Outcome`` assertions pin down when the page
must redraw.
"""
import pytest

from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS, with_default_parameters
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core import run_control
from ui.core.form import BOX_FORM_KEYS, collect_form_params
from ui.core.state import GuiSession, Outcome
from ui.worker_cap import WorkerCap

JOB_ID = "job_20261008_000000_abcdef"
PARAMS = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [20.0, 20.0, 20.0]}


class FakeBridge:
    def __init__(self, result=None, error=None, running=False, lines=()):
        self._result, self._error, self._running = result, error, running
        self._lines = list(lines)
        self.started_with = None
        self.stop_calls = 0

    def start_workflow(self, *args, **kwargs):
        self.started_with = (args, kwargs)

    def is_running(self):
        return self._running

    def request_stop(self):
        self.stop_calls += 1

    def get_log_lines(self):
        lines, self._lines = self._lines, []
        return lines

    def get_result(self):
        return self._result

    def get_error(self):
        return self._error


class BridgeFactory:
    def __init__(self):
        self.made = []

    def __call__(self):
        bridge = FakeBridge()
        self.made.append(bridge)
        return bridge


@pytest.fixture
def state():
    return GuiSession(worker_cap=WorkerCap(10, "compose"))


@pytest.fixture
def factory():
    return BridgeFactory()


def _fill_form(state, params=PARAMS):
    state.form["input_mode"] = "rcsb"
    state.form["param_protein_id"] = params["protein_id"]
    state.form["param_ligand_ids_text"] = ", ".join(params["ligand_ids"])
    for key, value in zip(BOX_FORM_KEYS, (*params["center"], *params["box_size"])):
        state.form[key] = str(value)


def _active_completed_session(state, tmp_path):
    """An active session whose checkpoint equals what the form will send."""
    _fill_form(state)
    checkpoint = with_default_parameters(collect_form_params(state))
    sm = WorkflowStateManager(tmp_path / JOB_ID)
    sm.save_parameters_checkpoint(checkpoint, source="initial_parse")
    sm.save_execution_state_checkpoint({"protein_file": "/x/p.pdbqt"}, step="visualize")
    state.interactive_session.activate(
        JOB_ID, sm, last_results={"status": "completed", "services_executed": list(WORKFLOW_STEPS)}
    )
    state.job_id = state.viewed_job_id = JOB_ID
    state.job_done = True
    return sm


def _last_reply(state):
    return state.messages[-1]["content"]


# ---------------------------------------------------------------------------
# New run
# ---------------------------------------------------------------------------

def test_run_with_missing_params_replies_without_an_error(state, tmp_path, factory):
    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome == Outcome()
    assert _last_reply(state).startswith("Cannot start — required parameters missing")
    assert factory.made == [] and state.job_id is None


def test_run_starts_a_job_and_checkpoints(state, tmp_path, factory):
    _fill_form(state)
    state.form["param_job_name"] = "  trial  "
    state.box_picker_receptor_pdb = "/x/old_protein.pdb"

    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome == Outcome()
    assert state.job_id and state.job_id == state.viewed_job_id
    assert state.job_name == "trial"
    assert state.box_picker_receptor_pdb is None  # belonged to an earlier protein
    assert state.bridge is factory.made[0]
    args, kwargs = state.bridge.started_with
    assert args[1] == state.job_id and args[2] == str(tmp_path)
    assert kwargs["job_name"] == "trial"
    sm = state.interactive_session.state_manager
    assert sm.load_latest_parameters()["protein_id"] == "6lu7"
    assert state.interactive_session.job_id == state.job_id
    assert _last_reply(state) == f"Starting docking session `{state.job_id}`..."


def test_run_validation_error_is_returned_inline(state, tmp_path, factory):
    state.parser = None  # validate_workflow raises

    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome.error.startswith("Validation error:")


# ---------------------------------------------------------------------------
# Redock
# ---------------------------------------------------------------------------

def test_redock_with_nothing_changed_replies_without_an_error(state, tmp_path, factory):
    _active_completed_session(state, tmp_path)

    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome == Outcome()
    assert _last_reply(state) == "No parameter changes detected — nothing to redock."
    assert state.pending_rerun is None


def test_redock_after_a_change_stages_a_plan_and_checkpoints(state, tmp_path, factory):
    sm = _active_completed_session(state, tmp_path)
    state.form["param_center_x"] = "9.0"

    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome == Outcome()
    assert state.pending_rerun["job_id"] == JOB_ID
    assert state.pending_rerun["steps"] == ["dock", "visualize"]
    assert sm.load_latest_parameters()["center"] == [9.0, 2.0, 3.0]
    assert "center" in state.interactive_session.pending_changed_params
    assert factory.made == []  # nothing runs before Confirm


def test_redock_after_stopped_first_run_docks_again(state, tmp_path, factory):
    _active_completed_session(state, tmp_path)
    state.interactive_session.last_results = {
        "status": "cancelled", "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein"],
    }

    run_control.run_docking(state, tmp_path, factory)

    assert state.pending_rerun["steps"] == ["dock", "visualize"]


def test_confirm_runs_the_staged_plan(state, tmp_path, factory):
    sm = _active_completed_session(state, tmp_path)
    state.job_name = "trial"
    state.form["param_center_x"] = "9.0"
    run_control.run_docking(state, tmp_path, factory)
    state.run_log.append("the first run's line")

    outcome = run_control.confirm_redock(state, tmp_path, factory)

    assert outcome == Outcome()
    assert state.pending_rerun is None and state.job_done is False
    assert state.run_log == []
    args, kwargs = state.bridge.started_with
    assert args[0]["services"] == ["dock", "visualize"]
    assert kwargs == {"job_name": "trial", "state_manager": sm}
    assert _last_reply(state).startswith(f"Redocking `{JOB_ID}` — steps: dock, visualize")


def test_confirm_after_the_active_session_changed_is_refused(state, tmp_path, factory):
    _active_completed_session(state, tmp_path)
    state.pending_rerun = {"job_id": "job_other", "steps": ["dock"], "params": {}}

    outcome = run_control.confirm_redock(state, tmp_path, factory)

    assert "different session" in outcome.error
    assert state.pending_rerun is None and factory.made == []


def test_confirm_without_a_plan_does_nothing(state, tmp_path, factory):
    assert run_control.confirm_redock(state, tmp_path, factory) == Outcome()


def test_cancel_drops_the_plan_but_keeps_it_plannable(state, tmp_path, factory):
    _active_completed_session(state, tmp_path)
    state.form["param_center_x"] = "9.0"
    run_control.run_docking(state, tmp_path, factory)
    steps = state.pending_rerun["steps"]

    assert run_control.cancel_redock(state) == Outcome()
    assert state.pending_rerun is None
    run_control.run_docking(state, tmp_path, factory)  # form now equals the checkpoint
    assert state.pending_rerun["steps"] == steps


def test_redock_with_missing_required_param_returns_error(state, tmp_path, factory):
    _active_completed_session(state, tmp_path)
    state.form["param_protein_id"] = ""
    state.params.pop("protein_id", None)
    state.form["param_center_x"] = "9.0"

    outcome = run_control.run_docking(state, tmp_path, factory)

    assert outcome.error.startswith("Cannot redock — required parameters are missing: ")
    assert "protein_id" in outcome.error
    assert state.pending_rerun is None


# ---------------------------------------------------------------------------
# Stop / save session / bridge state
# ---------------------------------------------------------------------------

def test_stop_requests_stop_of_a_running_bridge(state):
    state.bridge = FakeBridge(running=True)
    assert run_control.stop_run(state) == Outcome()
    assert state.bridge.stop_calls == 1


def test_stop_of_a_finished_bridge_does_nothing(state):
    state.bridge = FakeBridge()
    run_control.stop_run(state)
    assert state.bridge.stop_calls == 0


def test_save_session_with_no_change_clears_a_stale_draft(state, tmp_path):
    sm = _active_completed_session(state, tmp_path)
    sm.save_draft_parameters({**PARAMS, "center": [8.0, 8.0, 8.0]})

    assert run_control.save_session(state) == Outcome()
    assert _last_reply(state) == "No parameter changes to save."
    assert sm.load_draft_parameters() is None


def test_save_session_saves_a_change_as_draft(state, tmp_path):
    sm = _active_completed_session(state, tmp_path)
    state.form["param_center_x"] = "8.5"

    run_control.save_session(state)

    assert _last_reply(state) == "Parameters saved. Click **Run Docking** to apply them."
    assert sm.load_draft_parameters()["center"] == [8.5, 2.0, 3.0]
    assert sm.get_parameters_revision() == 1  # a draft is not a revision


def test_save_session_without_active_job_does_nothing(state):
    before = list(state.messages)
    assert run_control.save_session(state) == Outcome()
    assert state.messages == before


def test_active_bridge_job_id_and_any_bridge_running(state):
    state.job_id = JOB_ID
    assert run_control.active_bridge_job_id(state) is None
    assert not run_control.any_bridge_running(state)

    state.box_picker_bridge = FakeBridge(running=True)
    assert run_control.any_bridge_running(state)
    assert run_control.active_bridge_job_id(state) is None  # only the docking bridge counts

    state.bridge = FakeBridge(running=True)
    assert run_control.active_bridge_job_id(state) == JOB_ID


# ---------------------------------------------------------------------------
# process_docking_bridge
# ---------------------------------------------------------------------------

def test_running_bridge_collects_lines_into_the_run_log(state):
    state.bridge = FakeBridge(running=True, lines=["step 1", "step 2"])

    run_control.process_docking_bridge(state)
    state.bridge._lines = ["step 3"]
    run_control.process_docking_bridge(state)

    assert state.run_log == ["step 1", "step 2", "step 3"]
    assert state.job_done is False


def test_chat_message_sent_mid_run_gets_no_log_lines(state):
    # Review Stage R1 (Phase 16), deferred #1: the lines went to whatever
    # message was posted last, so a chat sent mid-run got the log appended.
    state.bridge = FakeBridge(running=True, lines=["step 1"])
    state.messages.append({"role": "user", "content": "show history"})

    run_control.process_docking_bridge(state)

    assert _last_reply(state) == "show history"
    assert state.run_log == ["step 1"]


def test_finished_bridge_collects_its_last_lines_before_the_reply(state):
    # Review Stage R1 (Phase 16): lines queued just before the thread ended
    # were never drained, so "WORKFLOW COMPLETED SUCCESSFULLY" never showed.
    state.bridge = FakeBridge(result={"status": "completed", "results": {}},
                              lines=["", "WORKFLOW COMPLETED SUCCESSFULLY"])

    run_control.process_docking_bridge(state)

    assert state.run_log == ["", "WORKFLOW COMPLETED SUCCESSFULLY"]
    assert _last_reply(state) == "Workflow completed successfully!"


def test_each_run_starts_a_new_run_log(state, tmp_path, factory):
    _fill_form(state)
    state.run_log.append("an earlier run's line")
    earlier = state.run_log

    run_control.run_docking(state, tmp_path, factory)

    assert state.run_log == [] and state.run_log is not earlier


@pytest.mark.parametrize(
    "result, error, expected",
    [
        ({"status": "completed", "results": {}}, None, "Workflow completed successfully!"),
        ({"status": "failed", "errors": ["dock exploded"]}, None, "Workflow failed: dock exploded"),
        ({"status": "failed", "errors": []}, None, "Workflow failed: unknown error"),
        ({"status": "cancelled"}, None, "Session stopped."),
        (None, "boom", "Workflow failed: boom"),
    ],
)
def test_finished_bridge_replies_exactly_once(state, result, error, expected):
    state.bridge = FakeBridge(result=result, error=error)

    run_control.process_docking_bridge(state)
    run_control.process_docking_bridge(state)

    assert state.job_done is True
    assert [m["content"] for m in state.messages].count(expected) == 1


def test_finished_bridge_with_unknown_status_waits(state):
    state.bridge = FakeBridge(result={"status": "running"})
    run_control.process_docking_bridge(state)
    assert state.job_done is False


@pytest.mark.parametrize("status, cleared", [("completed", True), ("cancelled", False), ("failed", False)])
def test_finished_run_clears_pending_only_when_completed(state, tmp_path, status, cleared):
    _active_completed_session(state, tmp_path)
    state.interactive_session.pending_changed_params = {"center"}
    state.job_done = False
    result = {"status": status, "results": {}, "errors": ["x"]}
    state.bridge = FakeBridge(result=result)

    run_control.process_docking_bridge(state)

    assert state.interactive_session.pending_changed_params == (set() if cleared else {"center"})
    assert state.interactive_session.last_results is result


def test_finished_run_of_another_job_does_not_overwrite_last_results(state, tmp_path):
    _active_completed_session(state, tmp_path)
    before = state.interactive_session.last_results
    state.job_id = "job_other"
    state.job_done = False
    state.bridge = FakeBridge(result={"status": "completed", "results": {}})

    run_control.process_docking_bridge(state)

    assert state.interactive_session.last_results is before


def test_no_bridge_is_a_noop(state):
    run_control.process_docking_bridge(state)
    assert len(state.messages) == 1


def test_process_docking_bridge_reports_whether_the_state_changed(state):
    # NiceGUI redraws on this (Review Stage R2): a run that ended before
    # any tick saw it running must still count as a change.
    assert run_control.process_docking_bridge(state) is False  # no bridge
    state.bridge = FakeBridge(running=True)
    assert run_control.process_docking_bridge(state) is False  # running, no news
    state.bridge = FakeBridge(running=True, lines=["step 1"])
    assert run_control.process_docking_bridge(state) is True
    state.bridge = FakeBridge(result={"status": "completed", "results": {}})
    assert run_control.process_docking_bridge(state) is True
    assert run_control.process_docking_bridge(state) is False  # already handled
