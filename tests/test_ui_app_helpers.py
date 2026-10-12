"""Tests for ui/app.py's non-widget helper functions.

Same streamlit dependency/invocation notes as tests/test_ui_session_state.py
apply here -- see that file's module docstring. Scope: this covers the
pure-logic helpers (form-value collection, the Phase 12 protein-geometry
chat handlers), called directly. The widget-rendering functions (`_render_*`)
are covered by tests/test_ui_apptest.py instead, which runs the whole page
through Streamlit's AppTest harness.
"""
import os
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")

import ui.app as app
from common.pdb_geometry import compute_protein_center_and_box
from services.llm_orchestrator.interactive_session import InteractiveSession
from services.llm_orchestrator.workflow_parser import WorkflowParser

FIXTURE_PDB = Path(__file__).parent / "fixtures" / "mini_protein.pdb"


@pytest.fixture(autouse=True)
def _clean_session_state():
    st.session_state.clear()
    # The page's init() always sets this before any handler runs, and
    # ui/core relies on it (no None guards since Phase 16 Review Stage R1).
    st.session_state["interactive_session"] = InteractiveSession()
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
# Box picker writes into the Docking Box fields -- the only box state
# (Stage 13.5): _apply_viewer_click, _step_box_size
# ---------------------------------------------------------------------------

def test_apply_viewer_click_writes_center_and_keeps_a_set_size():
    st.session_state.param_box_x = "30"
    st.session_state.param_box_y = "31"
    st.session_state.param_box_z = "32"

    app._apply_viewer_click([1.0, 2.5, -3.25])

    assert [st.session_state[k] for k in app._BOX_WIDGET_KEYS] == [
        "1.000", "2.500", "-3.250", "30", "31", "32",
    ]


def test_apply_viewer_click_fills_in_the_shown_size_when_unset():
    st.session_state.param_box_x = "30"  # y/z unset -> the size isn't set

    app._apply_viewer_click([1.0, 2.0, 3.0])

    assert [st.session_state[k] for k in app._BOX_WIDGET_KEYS[3:]] == ["20.000"] * 3


def _set_box_size(x, y, z):
    st.session_state.param_box_x, st.session_state.param_box_y, st.session_state.param_box_z = x, y, z


def _box_size_fields():
    return [st.session_state[k] for k in app._BOX_WIDGET_KEYS[3:]]


def test_apply_viewer_click_replaces_an_undrawable_size_with_the_placeholder():
    # The viewer draws the placeholder for "0, 20, 20" -- so that is what a
    # click must dock (Phase 14 R1), not the 0 it kept before.
    _set_box_size("0", "20", "20")

    app._apply_viewer_click([1.0, 2.0, 3.0])

    assert _box_size_fields() == ["20.000"] * 3


def test_step_box_size_changes_one_axis():
    _set_box_size("25", "25", "25")

    app._step_box_size(1, 1.0)

    assert _box_size_fields() == ["25", "26.000", "25"]


def test_step_box_size_stops_at_the_minimum():
    _set_box_size("20", "20", "1.5")

    app._step_box_size(2, -1.0)

    assert st.session_state["param_box_z"] == "1.000"


@pytest.mark.parametrize("sizes", [("", "", ""), ("abc", "20", "20"), ("0", "20", "20"), ("25", "", "")])
def test_step_box_size_steps_from_the_placeholder_when_undrawable(sizes):
    # Stepping one axis of a size the viewer can't draw left it undrawable
    # (the button seemed dead); the step now fills in the placeholder it
    # was showing on all three axes (Phase 14 R1).
    _set_box_size(*sizes)

    app._step_box_size(0, 1.0)

    assert _box_size_fields() == ["21.000", "20.000", "20.000"]


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
    assert st.session_state.get("_pending_geometry_targets") is None


def test_handle_protein_geometry_command_defers_when_fetch_starts(monkeypatch):
    # A protein source is already set (e.g. "Download 6lu7 and dock ATP...")
    # but nothing has been fetched/prepared yet -- the command should start
    # the fetch (mocked here to avoid a real background job/thread) and
    # defer itself instead of failing outright.
    st.session_state.messages = []
    st.session_state.params = {"protein_id": "6lu7"}

    monkeypatch.setattr(app, "_start_box_picker_prep", lambda: (True, None, ""))

    app._handle_protein_geometry_command("box_size")

    assert st.session_state["_pending_geometry_targets"] == ("box_size",)
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


# ---------------------------------------------------------------------------
# Stage 15.2: docking_algorithm from GUI chat
# ---------------------------------------------------------------------------

def _boom(*args, **kwargs):
    raise AssertionError("LLM parser must not be called for an algorithm command")


def test_on_chat_submit_algorithm_command_skips_llm(monkeypatch):
    st.session_state.messages = []
    st.session_state.params = {}
    monkeypatch.setattr(app, "parse_workflow_with_llm", _boom)
    monkeypatch.setattr(app, "parse_followup_with_llm", _boom)

    app._on_chat_submit("dock with vina")

    assert st.session_state.params["docking_algorithm"] == "vina"
    reply = st.session_state.messages[-1]["content"]
    assert "Vina (Monte Carlo)" in reply
    assert "Run Docking" in reply


def _setup_llm_chat(monkeypatch, followup_params):
    """A prior chat turn exists, so the follow-up LLM path runs."""
    st.session_state.messages = []
    st.session_state.params = {"docking_algorithm": "vina"}
    parser = WorkflowParser()
    parser.validate_workflow({"services": ["dock"], "parameters": {"protein_id": "6lu7"}})
    st.session_state.parser = parser
    st.session_state.llm_client = object()
    monkeypatch.setattr(app, "parse_workflow_with_llm", _boom)
    monkeypatch.setattr(
        app, "parse_followup_with_llm",
        lambda *_a, **_k: {"services_add": [], "parameters": dict(followup_params)},
    )


def test_on_chat_submit_llm_default_does_not_reset_algorithm(monkeypatch):
    """Finding 8: the validator's default "moldina" (also carried in the
    prior turn's parameters) must not overwrite a chosen algorithm."""
    _setup_llm_chat(monkeypatch, {"ph": 6.5})

    app._on_chat_submit("set pH to 6.5")

    assert st.session_state.params["docking_algorithm"] == "vina"
    assert st.session_state.params["ph"] == 6.5
    assert "Algorithm" not in st.session_state.messages[-1]["content"]


def test_on_chat_submit_llm_explicit_algorithm_applied_and_echoed(monkeypatch):
    _setup_llm_chat(monkeypatch, {"docking_algorithm": "PSO"})

    app._on_chat_submit("and run it with particle swarm please")

    assert st.session_state.params["docking_algorithm"] == "moldina"
    assert "- Algorithm: **Moldina (PSO)**" in st.session_state.messages[-1]["content"]


def test_on_chat_submit_first_parse_without_algorithm_keeps_choice(monkeypatch):
    st.session_state.messages = []
    st.session_state.params = {"docking_algorithm": "vina"}
    st.session_state.parser = WorkflowParser()
    st.session_state.llm_client = object()
    monkeypatch.setattr(app, "parse_followup_with_llm", _boom)
    monkeypatch.setattr(
        app, "parse_workflow_with_llm",
        lambda *_a, **_k: {"services": ["dock"], "parameters": {"protein_id": "6lu7"}},
    )

    app._on_chat_submit("dock 6lu7")

    assert st.session_state.params["docking_algorithm"] == "vina"
    assert st.session_state.params["protein_id"] == "6lu7"


# ---------------------------------------------------------------------------
# _save_upload (Phase 16, Stage 16.3: shared upload dir)
# ---------------------------------------------------------------------------

class _Upload:
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getbuffer(self):
        return memoryview(self._data)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_save_upload_creates_an_upload_dir_every_uid_can_write(tmp_path, umask_022):
    # The root Streamlit UI and the non-root NiceGUI UI upload into the
    # same directory.
    upload_dir = tmp_path / "uploads"

    saved = app._save_upload(_Upload("../../receptor.pdb", b"ATOM"), upload_dir)

    assert saved == str(upload_dir / "receptor.pdb")
    assert (upload_dir / "receptor.pdb").read_bytes() == b"ATOM"
    assert upload_dir.stat().st_mode & 0o777 == 0o777
