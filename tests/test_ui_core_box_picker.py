"""Tests for ui/core/box_picker.py (Stage 16.2) -- hermetic, plain GuiSession.

The Streamlit page's own wiring over the same code is covered by
tests/test_ui_app_helpers.py and tests/test_ui_apptest.py (ui tier).
"""
from pathlib import Path

import pytest

from common.pdb_geometry import compute_protein_center_and_box
from services.llm_orchestrator.workflow_parser import WorkflowParser
from ui.core import box_picker
from ui.core.form import BOX_FORM_KEYS
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap

FIXTURE_PDB = Path(__file__).parent / "fixtures" / "mini_protein.pdb"


class FakeBridge:
    def __init__(self, result=None, error=None, running=False):
        self._result, self._error, self._running = result, error, running
        self.started_with = None

    def start_workflow(self, *args, **kwargs):
        self.started_with = (args, kwargs)

    def is_running(self):
        return self._running

    def get_result(self):
        return self._result

    def get_error(self):
        return self._error


@pytest.fixture
def state():
    return GuiSession(worker_cap=WorkerCap(10, "compose"))


@pytest.fixture
def receptor(tmp_path):
    if not FIXTURE_PDB.exists():
        pytest.skip("Fixture file not available")
    path = tmp_path / "receptor.pdb"
    path.write_text(FIXTURE_PDB.read_text())
    return path


def _box(state):
    return [state.form[k] for k in BOX_FORM_KEYS]


def _set_size(state, x, y, z):
    state.form.update(param_box_x=x, param_box_y=y, param_box_z=z)


def _prepared(pdb_path):
    return {"status": "completed", "results": {"prepare_protein": {"prepared_proteins": [{"pdb_path": str(pdb_path)}]}}}


# ---------------------------------------------------------------------------
# Docking Box fields: viewer click, − / + steps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sizes,drawable", [
    (("30", "31", "32"), [30.0, 31.0, 32.0]),
    (("", "", ""), None),
    (("0", "20", "20"), None),
    (("abc", "20", "20"), None),
])
def test_drawable_box_size(state, sizes, drawable):
    _set_size(state, *sizes)
    assert box_picker.drawable_box_size(state) == drawable


def test_viewer_click_writes_center_and_keeps_a_drawable_size(state):
    _set_size(state, "30", "31", "32")
    box_picker.apply_viewer_click(state, [1.0, 2.5, -3.25])
    assert _box(state) == ["1.000", "2.500", "-3.250", "30", "31", "32"]


def test_viewer_click_fills_in_the_placeholder_size(state):
    _set_size(state, "0", "20", "20")
    box_picker.apply_viewer_click(state, [1.0, 2.0, 3.0])
    assert _box(state)[3:] == ["20.000"] * 3


def test_step_changes_one_axis_and_stops_at_the_minimum(state):
    _set_size(state, "25", "25", "1.5")
    box_picker.step_box_size(state, 1, 1.0)
    box_picker.step_box_size(state, 2, -1.0)
    assert _box(state)[3:] == ["25", "26.000", "1.000"]


def test_step_from_the_placeholder_writes_all_three_axes(state):
    box_picker.step_box_size(state, 0, 1.0)
    assert _box(state)[3:] == ["21.000", "20.000", "20.000"]


# ---------------------------------------------------------------------------
# start_box_picker_prep
# ---------------------------------------------------------------------------

def test_prep_without_a_protein_source_warns_and_starts_nothing(state, tmp_path):
    made = []
    started, message, severity = box_picker.start_box_picker_prep(
        state, tmp_path, lambda: made.append(FakeBridge()) or made[-1]
    )
    assert (started, severity) == (False, "warning")
    assert "missing" in message
    assert made == [] and state.box_picker_bridge is None


def test_prep_validation_error_is_reported(state, tmp_path):
    state.parser = None
    started, message, severity = box_picker.start_box_picker_prep(state, tmp_path, FakeBridge)
    assert (started, severity) == (False, "error")
    assert message.startswith("Validation error")


def test_prep_starts_the_prep_services_and_clears_the_old_picker_state(state, tmp_path):
    state.form.update(input_mode="rcsb", param_protein_id="6lu7")
    state.box_picker_receptor_pdb = "/old/receptor.pdb"
    state.pending_geometry_targets = ("center",)

    started, message, _ = box_picker.start_box_picker_prep(state, tmp_path, FakeBridge)

    assert started is True and message is None
    bridge = state.box_picker_bridge
    args, kwargs = bridge.started_with
    assert args[1] == state.box_picker_job_id and args[2] == str(tmp_path)
    assert kwargs == {"services": ["get_input_files", "prepare_protein"]}
    assert state.box_picker_receptor_pdb is None and state.pending_geometry_targets is None
    assert state.params["protein_id"] == "6lu7"


# ---------------------------------------------------------------------------
# find_receptor_pdb, apply_protein_geometry
# ---------------------------------------------------------------------------

def test_find_receptor_prefers_the_picker_then_the_last_run(state, tmp_path, receptor):
    other = tmp_path / "other.pdb"
    other.write_text("x")
    state.interactive_session.last_results = _prepared(other)
    assert box_picker.find_receptor_pdb(state, tmp_path) == other

    state.box_picker_receptor_pdb = str(receptor)
    assert box_picker.find_receptor_pdb(state, tmp_path) == receptor


def test_find_receptor_none_when_nothing_loaded_or_file_gone(state, tmp_path):
    assert box_picker.find_receptor_pdb(state, tmp_path) is None
    state.box_picker_receptor_pdb = str(tmp_path / "gone.pdb")
    assert box_picker.find_receptor_pdb(state, tmp_path) is None


def test_find_receptor_maps_the_services_mount_onto_output_base(state, tmp_path):
    (tmp_path / "job_x").mkdir()
    (tmp_path / "job_x" / "r.pdb").write_text("x")
    state.box_picker_receptor_pdb = "/app/orchestrator_output/job_x/r.pdb"
    assert box_picker.find_receptor_pdb(state, tmp_path) == tmp_path / "job_x" / "r.pdb"


@pytest.mark.parametrize("target,key,reply", [
    ("center", "center", "Protein center set to"),
    ("box_size", "box_size", "cover the whole protein"),
])
def test_apply_geometry_sets_one_field_group(state, receptor, target, key, reply):
    center, box = compute_protein_center_and_box(receptor.read_text())

    box_picker.apply_protein_geometry(state, receptor, target)

    expected = center if target == "center" else box
    assert state.params == {key: expected}
    field = "param_center_x" if target == "center" else "param_box_x"
    assert state.form[field] == str(expected[0])
    assert reply in state.messages[-1]["content"]


def test_apply_geometry_reports_an_unreadable_pdb(state, tmp_path):
    empty = tmp_path / "empty.pdb"
    empty.write_text("HEADER    NO ATOM RECORDS\nEND\n")
    box_picker.apply_protein_geometry(state, empty, "center")
    box_picker.apply_protein_geometry(state, tmp_path / "gone.pdb", "center")
    assert state.params == {}
    assert all("Could not compute protein geometry" in m["content"] for m in state.messages[-2:])


# ---------------------------------------------------------------------------
# process_box_picker_bridge
# ---------------------------------------------------------------------------

def test_running_prep_is_left_alone(state, tmp_path):
    bridge = FakeBridge(running=True)
    state.box_picker_bridge = bridge
    state.pending_geometry_targets = ("center",)
    box_picker.process_box_picker_bridge(state, tmp_path)
    assert state.box_picker_bridge is bridge and state.pending_geometry_targets == ("center",)


def test_completed_prep_records_the_receptor_and_applies_a_pending_command(state, tmp_path, receptor):
    state.box_picker_bridge = FakeBridge(result=_prepared(receptor))
    state.pending_geometry_targets = ("center",)
    greeting_count = len(state.messages)

    box_picker.process_box_picker_bridge(state, tmp_path)

    assert state.box_picker_receptor_pdb == str(receptor)
    assert "center" in state.params
    assert "Protein center set to" in state.messages[-1]["content"]
    assert len(state.messages) == greeting_count + 1
    assert state.box_picker_bridge is None and state.pending_geometry_targets is None
    assert state.box_picker_error is None


def test_completed_prep_applies_every_queued_command_in_order(state, tmp_path, receptor):
    # Review Stage R1 (Phase 16): with one slot, "center" then "whole
    # protein" sent during a prep set only the box size.
    state.box_picker_bridge = FakeBridge(result=_prepared(receptor))
    state.pending_geometry_targets = ("center", "box_size")

    box_picker.process_box_picker_bridge(state, tmp_path)

    assert "center" in state.params and "box_size" in state.params
    replies = [m["content"] for m in state.messages[-2:]]
    assert replies[0].startswith("Protein center set to")
    assert replies[1].startswith("Box size set to")


def test_completed_prep_without_a_pending_command_posts_nothing(state, tmp_path, receptor):
    state.box_picker_bridge = FakeBridge(result=_prepared(receptor))
    before = list(state.messages)
    box_picker.process_box_picker_bridge(state, tmp_path)
    assert state.messages == before
    assert state.params == {}


@pytest.mark.parametrize("bridge,panel,chat", [
    (FakeBridge(error="boom"), "Could not prepare receptor: boom", "Could not fetch the protein: boom"),
    (FakeBridge(result={"status": "failed", "errors": ["e1", "e2"]}),
     "Could not prepare receptor: e1; e2", "Could not fetch the protein: e1; e2"),
    (FakeBridge(result={"status": "completed", "results": {}}),
     "Receptor was prepared but no PDB path was returned.", "no PDB path was returned -- could not set this"),
])
def test_failed_prep_sets_the_panel_error_and_answers_a_pending_command(state, tmp_path, bridge, panel, chat):
    state.box_picker_bridge = bridge
    state.pending_geometry_targets = ("box_size",)

    box_picker.process_box_picker_bridge(state, tmp_path)

    assert state.box_picker_error == panel
    assert chat in state.messages[-1]["content"]
    assert state.box_picker_bridge is None and state.pending_geometry_targets is None


def test_failed_prep_without_a_pending_command_only_sets_the_panel_error(state, tmp_path):
    state.box_picker_bridge = FakeBridge(error="boom")
    before = list(state.messages)
    box_picker.process_box_picker_bridge(state, tmp_path)
    assert state.box_picker_error == "Could not prepare receptor: boom"
    assert state.messages == before
