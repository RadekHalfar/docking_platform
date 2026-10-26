"""Tests for ui/core/sessions.py (Stage 16.1) -- hermetic, plain GuiSession."""
import json

import pytest

from services.llm_orchestrator.workflow_definition import CORE_PARAMS, WORKFLOW_STEPS
from services.llm_orchestrator.workflow_parser import WorkflowParser
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core import sessions
from ui.core.state import GREETING_MESSAGE, GuiSession
from ui.worker_cap import WorkerCap

JOB_ID = "job_20261008_000001_cafe01"
REV1 = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [20.0, 20.0, 20.0]}
REV2 = {**REV1, "center": [5.0, 6.0, 7.0]}


class _Bridge:
    def __init__(self, running):
        self._running = running

    def is_running(self):
        return self._running


def _validated(params):
    return WorkflowParser().validate_workflow(
        {"services": list(WORKFLOW_STEPS), "parameters": dict(params)}
    )["parameters"]


@pytest.fixture
def state():
    return GuiSession(worker_cap=WorkerCap(10, "compose"))


@pytest.fixture
def job(tmp_path):
    """A completed session on disk: rev 1, rev 2 (center changed), rev 3 = its run."""
    sm = WorkflowStateManager(tmp_path / JOB_ID)
    sm.save_parameters_checkpoint(_validated(REV1), source="initial_parse")
    sm.save_parameters_checkpoint(_validated(REV2), source="interactive_update")
    sm.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="visualize")
    results = {"status": "completed", "job_name": "trial_a", "services_executed": list(WORKFLOW_STEPS)}
    (tmp_path / JOB_ID / "workflow_results.json").write_text(json.dumps(results))
    return {"job_id": JOB_ID, "job_dir": str(tmp_path / JOB_ID), "job_name": "trial_a"}


def _sm(state):
    return state.interactive_session.state_manager


# ---------------------------------------------------------------------------
# load_workflow_results
# ---------------------------------------------------------------------------


def _rename(state, job, new_name):
    """The Sessions tab's Rename: the file write (off the event loop in the
    GUI), then the state update."""
    error = sessions.rename_session_file(job, new_name)
    if error is None:
        sessions.apply_session_rename(state, job, new_name)
    return error


def _delete(state, job):
    """The Sessions tab's Delete: the rmtree (off the event loop in the GUI),
    then the state update."""
    error = sessions.remove_session_dir(job)
    if error is None:
        sessions.forget_session(state, job)
    return error

def test_load_workflow_results_reads_or_returns_none(tmp_path, job):
    assert sessions.load_workflow_results(tmp_path, JOB_ID)["job_name"] == "trial_a"
    assert sessions.load_workflow_results(tmp_path, "job_missing") is None
    (tmp_path / JOB_ID / "workflow_results.json").write_text("{not json")
    assert sessions.load_workflow_results(tmp_path, JOB_ID) is None


# ---------------------------------------------------------------------------
# Load (resume_session)
# ---------------------------------------------------------------------------

def test_resume_activates_the_session_and_fills_the_form(state, tmp_path, job):
    state.pending_rerun = {"job_id": "job_previous", "steps": ["dock"], "params": {}}
    state.job_done = False

    sessions.resume_session(state, job, tmp_path)

    assert state.job_id == state.viewed_job_id == JOB_ID
    assert state.job_name == "trial_a"
    assert state.job_done is True
    assert state.pending_rerun is None
    assert state.interactive_session.job_id == JOB_ID
    assert state.interactive_session.last_results["status"] == "completed"
    assert state.interactive_session.pending_changed_params == set()
    assert state.form["param_center_x"] == "5.0"
    assert state.params["center"] == [5.0, 6.0, 7.0]
    # The parser is seeded, so a chat follow-up merges onto these parameters.
    assert state.parser.parsed_workflow["parameters"]["center"] == [5.0, 6.0, 7.0]


def test_resume_prefers_a_draft(state, tmp_path, job):
    WorkflowStateManager(tmp_path / JOB_ID).save_draft_parameters({**REV2, "center": [8.5, 6.0, 7.0]})

    sessions.resume_session(state, job, tmp_path)

    assert state.form["param_center_x"] == "8.5"


def test_resume_drops_the_previous_runs_finished_bridge(state, tmp_path, job):
    # Stage 16.7's real-data check: the finished bridge of the run before
    # stayed, and both GUIs of the time drew its result as the loaded session's.
    state.job_id = "job_previous"
    state.bridge = _Bridge(running=False)

    sessions.resume_session(state, job, tmp_path)

    assert state.bridge is None


def test_resume_of_never_run_parameters_marks_all_pending(state, tmp_path, job):
    WorkflowStateManager(tmp_path / JOB_ID).restore_revision(1)

    sessions.resume_session(state, job, tmp_path)

    assert state.interactive_session.pending_changed_params == set(CORE_PARAMS)


@pytest.mark.parametrize("protein_id, kept", [("6lu7", True), ("1abc", False)])
def test_resume_drops_the_picker_receptor_only_for_another_protein(state, tmp_path, job, protein_id, kept):
    state.params = {"protein_id": protein_id}
    state.box_picker_receptor_pdb = "/x/receptor.pdb"

    sessions.resume_session(state, job, tmp_path)

    assert (state.box_picker_receptor_pdb is not None) is kept


# ---------------------------------------------------------------------------
# Rename / delete
# ---------------------------------------------------------------------------

def test_rename_updates_the_viewed_sessions_label(state, tmp_path, job):
    state.viewed_job_id = JOB_ID

    assert _rename(state, job, "  renamed  ") is None

    assert state.job_name == "renamed"
    assert sessions.load_workflow_results(tmp_path, JOB_ID)["job_name"] == "renamed"


def test_rename_of_another_session_leaves_the_label(state, tmp_path, job):
    state.job_name = "mine"
    _rename(state, job, "renamed")
    assert state.job_name == "mine"


def test_rename_failure_is_returned(state, tmp_path):
    error = _rename(state, {"job_id": "job_x", "job_dir": str(tmp_path / "job_x")}, "n")
    assert error.startswith("Rename failed:")


def test_delete_of_the_active_session_starts_a_new_one(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)

    assert _delete(state, job) is None

    assert not (tmp_path / JOB_ID).exists()
    assert state.job_id is None and state.viewed_job_id is None
    assert not state.interactive_session.has_active_job()
    assert state.messages == [GREETING_MESSAGE]


def test_delete_of_another_session_keeps_the_active_one(state, tmp_path, job):
    state.job_id = state.viewed_job_id = "job_mine"
    state.messages.append({"role": "user", "content": "hi"})

    _delete(state, job)

    assert state.job_id == state.viewed_job_id == "job_mine"
    assert len(state.messages) == 2


def test_delete_failure_is_returned(state, tmp_path):
    error = _delete(state, {"job_id": "job_x", "job_dir": str(tmp_path / "job_x")})
    assert error.startswith("Delete failed:")


def test_rename_and_delete_come_in_two_halves(state, tmp_path, job):
    # Stage 16.7: NiceGUI runs the file half off its event loop.
    state.viewed_job_id = JOB_ID

    assert sessions.rename_session_file(job, " new ") is None
    assert state.job_name == ""  # the state half hasn't run
    sessions.apply_session_rename(state, job, " new ")
    assert state.job_name == "new"
    # The listing entry follows too: a Load of it before the GUI re-lists
    # resumed the old name (Review Stage R3).
    assert job["job_name"] == "new"

    assert sessions.remove_session_dir(job) is None
    assert not (tmp_path / JOB_ID).exists() and state.viewed_job_id == JOB_ID
    sessions.forget_session(state, job)
    assert state.viewed_job_id is None


# ---------------------------------------------------------------------------
# Listing (Stage 16.7)
# ---------------------------------------------------------------------------

def test_list_sessions_returns_completed_and_failed(tmp_path, job):
    failed = tmp_path / "job_20261008_000002_beef02"
    failed.mkdir()
    (failed / "workflow_results.json").write_text(json.dumps({"status": "cancelled", "errors": ["stopped"]}))

    completed, failed_jobs = sessions.list_sessions(tmp_path)

    assert [j["job_id"] for j in completed] == [JOB_ID]
    assert [j["job_id"] for j in failed_jobs] == [failed.name]


def test_session_row_labels():
    named = {"job_id": "job_1", "job_name": "my*run", "protein_id": "6LU7", "ligand_count": 2}
    assert sessions.session_row_label(named, sessions.completed_session_detail(named)) == (
        "**my\*run**  `job_1`  6LU7 (2 ligand(s))"
    )
    unnamed = {"job_id": "job_2", "job_name": "", "protein_id": "?", "first_error": "x" * 100}
    detail = sessions.failed_session_detail(unnamed)
    assert detail == "? — " + "x" * 77 + "..."
    assert sessions.session_row_label(unnamed, detail) == f"job_2  {detail}"
    assert sessions.failed_session_detail({"protein_id": "?"}) == "? — Unknown error"


def test_revision_panel_data(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)

    table, current, restorable = sessions.revision_panel_data(_sm(state))

    assert "| 1 |" in table and "| 3 |" in table
    assert current == 2
    assert restorable == [2, 1]  # rev 3 holds only the run's state


# ---------------------------------------------------------------------------
# Undo / history
# ---------------------------------------------------------------------------

def test_undo_without_active_session(state):
    assert sessions.undo_to_revision(state, 1) == "No active session — run or load one first."


def test_undo_refused_while_a_run_is_going(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)
    state.bridge = _Bridge(running=True)

    assert sessions.undo_to_revision(state, 1) == "A session is running — Stop it before restoring a revision."
    assert _sm(state).get_parameters_revision() == 2


def test_undo_of_unknown_and_state_only_revisions(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)

    assert sessions.undo_to_revision(state, 99).startswith("Cannot restore revision 99")
    assert "has no parameter checkpoint" in sessions.undo_to_revision(state, 3)
    assert _sm(state).get_parameters_revision() == 2


def test_undo_restores_the_form_and_marks_all_pending(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)
    _sm(state).save_draft_parameters({**REV2, "center": [9.0, 9.0, 9.0]})
    state.pending_rerun = {"job_id": JOB_ID, "steps": ["dock"], "params": {}}

    reply = sessions.undo_to_revision(state, 1)

    assert reply.startswith("Restored revision 1.")
    assert state.form["param_center_x"] == "1.0"
    assert _sm(state).load_latest_parameters()["center"] == [1.0, 2.0, 3.0]
    assert _sm(state).load_draft_parameters() is None
    assert state.pending_rerun is None
    assert state.interactive_session.pending_changed_params == set(CORE_PARAMS)


def test_history_chat_reply(state, tmp_path, job):
    assert sessions.history_chat_reply(state, {"intent": "show_history"}) == "No active session — no history yet."
    sessions.resume_session(state, job, tmp_path)

    shown = sessions.history_chat_reply(state, {"intent": "show_history"})
    assert "| 1 |" in shown and "| 2 |" in shown and "currently at revision **2**" in shown
    assert "Which revision?" in sessions.history_chat_reply(state, {"intent": "undo_revision", "name": None})
    assert sessions.history_chat_reply(state, {"intent": "undo_revision", "name": "1"}).startswith("Restored revision 1.")


def test_format_history_markdown_escapes_and_flags():
    assert sessions.format_history_markdown([]) == "No revisions recorded yet."
    table = sessions.format_history_markdown([
        {"revision": 1, "timestamp": "2026-10-08T10:00:00.123+00:00", "event_type": "initial_parse",
         "details": {"note": "a|b"}, "has_parameters": True, "has_state": False},
    ])
    assert "| 1 | 2026-10-08 10:00:00 | initial\\_parse | note=a\\|b \\[params\\] |" in table
