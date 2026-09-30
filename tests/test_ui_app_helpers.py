"""Tests for ui/app.py's non-widget helper functions.

Same streamlit dependency/invocation notes as tests/test_ui_session_state.py
apply here -- see that file's module docstring. Scope: this covers the
pure-logic helpers (form-value collection, the Phase 12 protein-geometry
chat handlers), not the widget-rendering functions (`_render_*`), which
call `st.button`/`st.columns`/etc. and need a real Streamlit script-run
context (or an AppTest-based harness) to exercise meaningfully -- out of
scope here, same as every other `_render_*` function in this file.
"""
import os
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")

import ui.app as app
from common.pdb_geometry import compute_protein_center_and_box
from services.llm_orchestrator.workflow_parser import WorkflowParser

FIXTURE_PDB = Path(__file__).parent / "fixtures" / "mini_protein.pdb"


@pytest.fixture(autouse=True)
def _clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


@pytest.fixture
def mini_protein_pdb_text():
    if not FIXTURE_PDB.exists():
        pytest.skip("Fixture file not available")
    return FIXTURE_PDB.read_text()


# ---------------------------------------------------------------------------
# _collect_form_params
# ---------------------------------------------------------------------------

def test_collect_form_params_rcsb_mode():
    st.session_state.input_mode = "rcsb"
    st.session_state.param_protein_id = "6lu7"
    st.session_state.param_ligand_ids_text = "ATP, HEM"
    st.session_state.param_center_x = "1.0"
    st.session_state.param_center_y = "2.0"
    st.session_state.param_center_z = "3.0"
    st.session_state.param_box_x = "10"
    st.session_state.param_box_y = "20"
    st.session_state.param_box_z = "30"

    params = app._collect_form_params()

    assert params["protein_id"] == "6lu7"
    assert params["ligand_ids"] == ["ATP", "HEM"]
    assert params["center"] == [1.0, 2.0, 3.0]
    assert params["box_size"] == [10.0, 20.0, 30.0]
    assert params["ph"] == 7.4
    assert params["exhaustiveness"] == 8
    assert params["cpu"] == 1
    assert params["parallel"] is False
    assert params["max_workers"] == 1


def test_collect_form_params_omits_center_when_incomplete():
    st.session_state.input_mode = "rcsb"
    st.session_state.param_center_x = "1.0"
    # y/z left unset -> float("") raises -> the whole center is omitted,
    # not partially populated.
    params = app._collect_form_params()
    assert "center" not in params


def test_collect_form_params_max_workers_clamped_to_at_least_1():
    st.session_state.param_max_workers = 0
    params = app._collect_form_params()
    assert params["max_workers"] == 1


def test_collect_form_params_folder_mode_skips_ligand_text_field():
    st.session_state.input_mode = "folder"
    st.session_state.param_protein_id = "6lu7"
    st.session_state.param_ligand_ids_text = "ATP"  # irrelevant in folder mode
    params = app._collect_form_params()
    assert params["protein_id"] == "6lu7"
    assert "ligand_ids" not in params


# ---------------------------------------------------------------------------
# _sync_picker_box_from_manual_inputs -- the unconditional variant used by
# _apply_protein_geometry, as opposed to _seed_picker_box_from_manual_inputs'
# guarded "only if nothing picked yet" version below.
# ---------------------------------------------------------------------------

def test_sync_picker_box_from_manual_inputs_overwrites_existing_pick():
    st.session_state.picked_center = [9.0, 9.0, 9.0]
    st.session_state.param_center_x = "1.0"
    st.session_state.param_center_y = "2.0"
    st.session_state.param_center_z = "3.0"

    app._sync_picker_box_from_manual_inputs()

    assert st.session_state["picked_center"] == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# _seed_picker_box_from_manual_inputs
# ---------------------------------------------------------------------------

def test_seed_picker_box_from_manual_inputs_seeds_both():
    st.session_state.param_center_x = "1.0"
    st.session_state.param_center_y = "2.0"
    st.session_state.param_center_z = "3.0"
    st.session_state.param_box_x = "10"
    st.session_state.param_box_y = "20"
    st.session_state.param_box_z = "30"

    app._seed_picker_box_from_manual_inputs()

    assert st.session_state["picked_center"] == [1.0, 2.0, 3.0]
    assert st.session_state["picker_box_x"] == 10.0
    assert st.session_state["picker_box_y"] == 20.0
    assert st.session_state["picker_box_z"] == 30.0
    assert st.session_state["_box_picker_last_size"] == [10.0, 20.0, 30.0]


def test_seed_picker_box_from_manual_inputs_partial_center_only():
    st.session_state.param_center_x = "1.0"
    st.session_state.param_center_y = "2.0"
    st.session_state.param_center_z = "3.0"
    # Box size fields left unset -> that half is skipped, not partially seeded.

    app._seed_picker_box_from_manual_inputs()

    assert st.session_state["picked_center"] == [1.0, 2.0, 3.0]
    assert "picker_box_x" not in st.session_state
    assert "_box_picker_last_size" not in st.session_state


def test_seed_picker_box_from_manual_inputs_noop_when_already_picked():
    st.session_state.picked_center = [9.0, 9.0, 9.0]
    st.session_state.param_center_x = "1.0"
    st.session_state.param_center_y = "2.0"
    st.session_state.param_center_z = "3.0"

    app._seed_picker_box_from_manual_inputs()

    assert st.session_state["picked_center"] == [9.0, 9.0, 9.0]


def test_seed_picker_box_from_manual_inputs_noop_when_manual_fields_empty():
    app._seed_picker_box_from_manual_inputs()

    assert st.session_state.get("picked_center") is None
    assert "picker_box_x" not in st.session_state


# ---------------------------------------------------------------------------
# _start_box_picker_prep
# ---------------------------------------------------------------------------

def test_start_box_picker_prep_reports_missing_protein_source():
    st.session_state.input_mode = "rcsb"
    st.session_state.params = {}
    st.session_state.parser = WorkflowParser()

    started, message, severity = app._start_box_picker_prep()

    assert started is False
    assert severity == "warning"
    assert "missing" in message


def test_start_box_picker_prep_reports_validation_error_without_a_parser():
    st.session_state.input_mode = "rcsb"
    st.session_state.params = {}
    # No st.session_state.parser -- validate_workflow() can't even be
    # reached; the AttributeError is caught and reported like any other
    # validation failure rather than crashing the chat handler.
    started, message, severity = app._start_box_picker_prep()

    assert started is False
    assert severity == "error"
    assert "Validation error" in message


# ---------------------------------------------------------------------------
# _find_receptor_pdb_for_geometry
# ---------------------------------------------------------------------------

def test_find_receptor_pdb_prefers_box_picker_receptor(tmp_path, mini_protein_pdb_text):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(mini_protein_pdb_text)
    st.session_state["_box_picker_receptor_pdb"] = str(receptor)

    found = app._find_receptor_pdb_for_geometry()
    assert found == receptor


def test_find_receptor_pdb_falls_back_to_session_last_results(tmp_path, mini_protein_pdb_text):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(mini_protein_pdb_text)

    class FakeSession:
        last_results = {
            "results": {
                "prepare_protein": {"prepared_proteins": [{"pdb_path": str(receptor)}]}
            }
        }

    st.session_state["interactive_session"] = FakeSession()

    found = app._find_receptor_pdb_for_geometry()
    assert found == receptor


def test_find_receptor_pdb_returns_none_when_nothing_loaded():
    assert app._find_receptor_pdb_for_geometry() is None


def test_find_receptor_pdb_returns_none_when_file_missing_on_disk(tmp_path):
    st.session_state["_box_picker_receptor_pdb"] = str(tmp_path / "gone.pdb")
    assert app._find_receptor_pdb_for_geometry() is None


# ---------------------------------------------------------------------------
# _handle_protein_geometry_command -- the GUI side of "set up box center as
# protein center" / "look across all protein" (Phase 12).
# ---------------------------------------------------------------------------

def test_handle_protein_geometry_command_no_protein_source_reports_whats_missing():
    # No protein_id/protein_file at all -- _start_box_picker_prep() can't
    # even start a fetch, so the geometry command reports exactly what's
    # missing instead of a generic "no protein loaded" message.
    st.session_state.messages = []
    st.session_state.params = {}
    st.session_state.parser = WorkflowParser()

    app._handle_protein_geometry_command("center")

    assert len(st.session_state.messages) == 1
    assert st.session_state.messages[0]["role"] == "assistant"
    assert "missing" in st.session_state.messages[0]["content"]
    assert st.session_state.get("_pending_geometry_target") is None


def test_handle_protein_geometry_command_defers_when_fetch_starts(monkeypatch):
    # A protein source is already set (e.g. "Download 6lu7 and dock ATP...")
    # but nothing has been fetched/prepared yet -- the command should start
    # the fetch (mocked here to avoid a real background job/thread) and
    # defer itself instead of failing outright.
    st.session_state.messages = []
    st.session_state.params = {"protein_id": "6lu7"}

    monkeypatch.setattr(app, "_start_box_picker_prep", lambda: (True, None, ""))

    app._handle_protein_geometry_command("box_size")

    assert st.session_state["_pending_geometry_target"] == "box_size"
    assert "fetching" in st.session_state.messages[-1]["content"].lower()


def test_handle_protein_geometry_command_center(tmp_path, mini_protein_pdb_text):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(mini_protein_pdb_text)
    st.session_state["_box_picker_receptor_pdb"] = str(receptor)
    st.session_state.messages = []
    st.session_state.params = {}

    app._handle_protein_geometry_command("center")

    expected_center, _ = compute_protein_center_and_box(mini_protein_pdb_text)
    assert st.session_state.params["center"] == expected_center
    assert st.session_state.param_center_x == str(expected_center[0])
    assert "box_size" not in st.session_state.params
    assert "Protein center set to" in st.session_state.messages[-1]["content"]
    # Regression: this command takes the immediate path (a receptor was
    # already loaded) -- no bridge transition runs, so the box picker's own
    # viewer state (picked_center) must be synced here directly, or the
    # picker keeps showing a stale/no box despite the manual fields being
    # correct (reported: "I don't see protein and docking box").
    assert st.session_state["picked_center"] == expected_center


def test_handle_protein_geometry_command_box_size(tmp_path, mini_protein_pdb_text):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(mini_protein_pdb_text)
    st.session_state["_box_picker_receptor_pdb"] = str(receptor)
    st.session_state.messages = []
    st.session_state.params = {}

    app._handle_protein_geometry_command("box_size")

    _, expected_box = compute_protein_center_and_box(mini_protein_pdb_text)
    assert st.session_state.params["box_size"] == expected_box
    assert st.session_state.param_box_x == str(expected_box[0])
    assert "center" not in st.session_state.params
    assert "cover the whole protein" in st.session_state.messages[-1]["content"]
    assert st.session_state["picker_box_x"] == expected_box[0]
    assert st.session_state["picker_box_y"] == expected_box[1]
    assert st.session_state["picker_box_z"] == expected_box[2]
    assert st.session_state["_box_picker_last_size"] == expected_box


def test_handle_protein_geometry_command_malformed_pdb_reports_error(tmp_path):
    receptor = tmp_path / "empty.pdb"
    receptor.write_text("HEADER    NO ATOM RECORDS\nEND\n")
    st.session_state["_box_picker_receptor_pdb"] = str(receptor)
    st.session_state.messages = []
    st.session_state.params = {}

    app._handle_protein_geometry_command("center")

    assert "center" not in st.session_state.params
    assert "Could not compute protein geometry" in st.session_state.messages[-1]["content"]


# ---------------------------------------------------------------------------
# detect_protein_geometry_command wiring in _on_chat_submit -- confirms the
# early-return-before-LLM path actually short-circuits (Review Stage R1
# checked this by inspection; this locks it in as a real test).
# ---------------------------------------------------------------------------

def test_on_chat_submit_geometry_command_does_not_touch_llm_parser(monkeypatch, tmp_path, mini_protein_pdb_text):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(mini_protein_pdb_text)
    st.session_state["_box_picker_receptor_pdb"] = str(receptor)
    st.session_state.messages = []
    st.session_state.params = {}

    def _boom(*args, **kwargs):
        raise AssertionError("LLM parser must not be called for a geometry command")

    monkeypatch.setattr(app, "parse_workflow_with_llm", _boom)
    monkeypatch.setattr(app, "parse_followup_with_llm", _boom)

    app._on_chat_submit("look across all protein")

    assert st.session_state.messages[0]["content"] == "look across all protein"
    assert "cover the whole protein" in st.session_state.messages[-1]["content"]
