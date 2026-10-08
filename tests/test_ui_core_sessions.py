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

    assert sessions.rename_session(state, job, "  renamed  ") is None

    assert state.job_name == "renamed"
    assert sessions.load_workflow_results(tmp_path, JOB_ID)["job_name"] == "renamed"


def test_rename_of_another_session_leaves_the_label(state, tmp_path, job):
    state.job_name = "mine"
    sessions.rename_session(state, job, "renamed")
    assert state.job_name == "mine"


def test_rename_failure_is_returned(state, tmp_path):
    error = sessions.rename_session(state, {"job_id": "job_x", "job_dir": str(tmp_path / "job_x")}, "n")
    assert error.startswith("Rename failed:")


def test_delete_of_the_active_session_starts_a_new_one(state, tmp_path, job):
    sessions.resume_session(state, job, tmp_path)

    assert sessions.delete_session(state, job) is None

    assert not (tmp_path / JOB_ID).exists()
    assert state.job_id is None and state.viewed_job_id is None
    assert not state.interactive_session.has_active_job()
    assert state.messages == [GREETING_MESSAGE]


def test_delete_of_another_session_keeps_the_active_one(state, tmp_path, job):
    state.job_id = state.viewed_job_id = "job_mine"
    state.messages.append({"role": "user", "content": "hi"})

    sessions.delete_session(state, job)

    assert state.job_id == state.viewed_job_id == "job_mine"
    assert len(state.messages) == 2


def test_delete_failure_is_returned(state, tmp_path):
    error = sessions.delete_session(state, {"job_id": "job_x", "job_dir": str(tmp_path / "job_x")})
    assert error.startswith("Delete failed:")


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
