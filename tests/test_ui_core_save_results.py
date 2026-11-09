"""Tests for ui/core/save_results.py -- hermetic, plain
GuiSession, a real WorkflowStateManager under tmp_path."""
import json
from pathlib import Path

import pytest

from services.llm_orchestrator.interactive_session import SAVED_RESULTS_DIRNAME
from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS
from services.llm_orchestrator.workflow_parser import WorkflowParser
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core import save_results
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap

from tests.ui_fakes import finish_inline

JOB_ID = "job_20261008_000002_beef02"
PARAMS = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [20.0, 20.0, 20.0]}


class _Bridge:
    def __init__(self, running):
        self._running = running

    def is_running(self):
        return self._running


@pytest.fixture
def state(tmp_path):
    """The active session: a completed run of its latest parameters."""
    s = GuiSession(worker_cap=WorkerCap(10, "compose"))
    job_dir = tmp_path / JOB_ID
    sm = WorkflowStateManager(job_dir)
    validated = WorkflowParser().validate_workflow({"services": list(WORKFLOW_STEPS), "parameters": dict(PARAMS)})
    sm.save_parameters_checkpoint(validated["parameters"], source="initial_parse")
    sm.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="visualize")
    (job_dir / "workflow_results.json").write_text(json.dumps({"status": "completed"}))
    (job_dir / "docking_results").mkdir()
    (job_dir / "docking_results" / "ATP_out.pdbqt").write_text("MODEL 1\n")
    s.interactive_session.activate(JOB_ID, sm)
    s.job_id = s.viewed_job_id = JOB_ID
    return s


def _bundles(tmp_path):
    saved = tmp_path / JOB_ID / SAVED_RESULTS_DIRNAME
    return sorted(p.name for p in saved.iterdir() if not p.name.startswith(".")) if saved.exists() else []


# ---------------------------------------------------------------------------
# save_block_reason
# ---------------------------------------------------------------------------

def test_a_completed_run_can_be_saved(state, tmp_path):
    assert save_results.save_block_reason(state, tmp_path) is None


def test_no_active_session_blocks(tmp_path):
    fresh = GuiSession(worker_cap=WorkerCap(10, "compose"))
    assert save_results.save_block_reason(fresh, tmp_path) == "No active session — run or load one first."


def test_a_running_session_blocks(state, tmp_path):
    state.bridge = _Bridge(running=True)
    assert "A session is running" in save_results.save_block_reason(state, tmp_path)


def test_pending_changes_block_and_are_named(state, tmp_path):
    state.interactive_session.pending_changed_params = {"ph", "center"}
    assert "(center, ph)" in save_results.save_block_reason(state, tmp_path)


def test_parameters_not_run_since_checkpoint_block(state, tmp_path):
    state.interactive_session.state_manager.save_parameters_checkpoint(
        {**PARAMS, "ph": 6.5}, source="interactive_update"
    )
    assert "have not been run yet" in save_results.save_block_reason(state, tmp_path)


@pytest.mark.parametrize("content", ['{"status": "failed"}', "{not json"])
def test_a_run_that_did_not_complete_blocks(state, tmp_path, content):
    (tmp_path / JOB_ID / "workflow_results.json").write_text(content)
    assert "did not complete" in save_results.save_block_reason(state, tmp_path)


# ---------------------------------------------------------------------------
# save_results and the name-clash flow
# ---------------------------------------------------------------------------

def test_save_writes_the_bundle_and_says_what_it_copied(state, tmp_path):
    reply = _save(state, tmp_path, "trial_a", "docking")

    assert reply.startswith("Saved bundle `trial_a` (scope: docking)")
    assert "Artifacts: docking_results, docking_box.json." in reply
    assert _bundles(tmp_path) == ["trial_a"]
    assert state.save_conflict is None


def test_blocked_or_invalid_saves_write_nothing(state, tmp_path):
    assert _save(state, tmp_path, "../x", "all").startswith("Invalid bundle name:")
    state.bridge = _Bridge(running=True)
    assert _save(state, tmp_path, "ok", "all").startswith("Cannot save: A session is running")
    assert _bundles(tmp_path) == []


def _save(state, output_base, name, scope, *, overwrite=False):
    """The panel's save, run inline (the GUI copies in ``run.io_bound``)."""
    return finish_inline(state, save_results.plan_save(state, output_base, name, scope, overwrite=overwrite))


def _resolve_conflict(state, output_base, *, overwrite):
    """The conflict buttons' path, run inline; the reply, or None with no
    conflict."""
    return finish_inline(state, save_results.plan_conflict_save(state, output_base, overwrite=overwrite))


def _chat_save(state, output_base, intent):
    return finish_inline(state, save_results.plan_chat_save(state, output_base, intent))


def test_a_name_clash_offers_overwrite_or_a_new_name(state, tmp_path):
    _save(state, tmp_path, "trial_a", "all")

    reply = _save(state, tmp_path, "trial_a", "docking")

    conflict = state.save_conflict
    assert conflict["job_id"] == JOB_ID and conflict["name"] == "trial_a" and conflict["scope"] == "docking"
    assert conflict["suggested"].startswith("trial_a_")
    assert f"**Save as {conflict['suggested']}**" in reply

    reply = _resolve_conflict(state, tmp_path, overwrite=False)
    assert reply.startswith(f"Saved bundle `{conflict['suggested']}`")
    assert state.save_conflict is None
    assert _bundles(tmp_path) == sorted(["trial_a", conflict["suggested"]])


def test_overwrite_replaces_the_bundle(state, tmp_path):
    _save(state, tmp_path, "trial_a", "all")
    _save(state, tmp_path, "trial_a", "docking")

    reply = _resolve_conflict(state, tmp_path, overwrite=True)

    assert reply.startswith("Saved bundle `trial_a` (scope: docking)")
    assert _bundles(tmp_path) == ["trial_a"]
    bundle = tmp_path / JOB_ID / SAVED_RESULTS_DIRNAME / "trial_a"
    assert json.loads((bundle / "metadata.json").read_text())["scope"] == "docking"


def test_cancel_drops_the_conflict_and_resolving_none_does_nothing(state, tmp_path):
    _save(state, tmp_path, "trial_a", "all")
    _save(state, tmp_path, "trial_a", "all")

    save_results.cancel_save_conflict(state)

    assert state.save_conflict is None
    assert _resolve_conflict(state, tmp_path, overwrite=True) is None
    assert _bundles(tmp_path) == ["trial_a"]


def test_plan_conflict_save_plans_without_copying(state, tmp_path):
    # NiceGUI plans on its event loop and copies elsewhere.
    _save(state, tmp_path, "trial_a", "all")
    _save(state, tmp_path, "trial_a", "docking")
    suggested = state.save_conflict["suggested"]

    _, overwrite = save_results.plan_conflict_save(state, tmp_path, overwrite=True)
    _, new_name = save_results.plan_conflict_save(state, tmp_path, overwrite=False)

    assert (overwrite.name, overwrite.scope, overwrite.overwrite) == ("trial_a", "docking", True)
    assert (new_name.name, new_name.scope, new_name.overwrite) == (suggested, "docking", False)
    assert _bundles(tmp_path) == ["trial_a"]  # nothing copied yet
    assert state.save_conflict is not None


def test_plan_conflict_save_without_a_conflict_or_while_blocked(state, tmp_path):
    assert save_results.plan_conflict_save(state, tmp_path, overwrite=True) == (None, None)

    state.save_conflict = {"job_id": JOB_ID, "name": "trial_a", "scope": "all", "suggested": "trial_a_1"}
    state.bridge = _Bridge(running=True)
    message, job = save_results.plan_conflict_save(state, tmp_path, overwrite=True)
    assert job is None and message.startswith("Cannot save: A session is running")


# ---------------------------------------------------------------------------
# The chat's save results/docking/visualization as <name>
# ---------------------------------------------------------------------------

def test_chat_save_needs_a_name_and_defaults_to_all(state, tmp_path):
    assert _chat_save(state, tmp_path, {"intent": "save_results", "name": None}) == (
        "Give the bundle a name, e.g. `save results as run1`."
    )
    reply = _chat_save(state, tmp_path, {"intent": "save_results", "name": "run1", "scope": None})
    assert reply.startswith("Saved bundle `run1` (scope: all)")


def test_scope_labels_cover_every_scope():
    assert set(save_results.SCOPE_LABELS) == set(save_results.SAVE_SCOPES)
