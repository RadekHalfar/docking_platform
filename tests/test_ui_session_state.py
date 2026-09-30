"""Tests for ui/session_state.py.

These require ``streamlit``, which is intentionally *not* installed in the
`script-runner` image (or in CI's `requirements.txt`) -- only the `ui`
container's own image has it (`Dockerfile.ui`, built `FROM
mcp-docking-base:latest` + streamlit/pandas/plotly). ``pytest.importorskip``
below makes this file a graceful no-op (reported as skipped, not an error)
under the default hermetic suite; run it for real via the one image that
actually has streamlit:

    docker compose run --rm ui python -m pytest tests/test_ui_session_state.py tests/test_ui_app_helpers.py -v

See docs/DOCKER_DEPLOYMENT.md's Testing section for the full command.
"""
import pytest

st = pytest.importorskip("streamlit")

from services.llm_orchestrator.interactive_session import InteractiveSession
from ui import session_state


@pytest.fixture(autouse=True)
def _clean_session_state():
    """st.session_state is a process-wide singleton (there's no real
    per-browser-tab isolation outside an actual Streamlit server) -- reset
    it around every test so tests can't see each other's state."""
    st.session_state.clear()
    yield
    st.session_state.clear()


# ---------------------------------------------------------------------------
# init()
# ---------------------------------------------------------------------------

def test_init_seeds_expected_defaults():
    session_state.init()
    assert st.session_state.messages == [dict(session_state._GREETING_MESSAGE)]
    assert st.session_state.params == {}
    assert st.session_state.input_mode == "local_files"
    assert st.session_state.job_id is None
    assert st.session_state.viewed_job_id is None
    assert st.session_state.bridge is None
    assert isinstance(st.session_state.interactive_session, InteractiveSession)
    assert not st.session_state.interactive_session.has_active_job()
    assert st.session_state.param_protein_id == ""
    assert st.session_state.param_ph == "7.4"
    assert st.session_state.param_exhaustiveness == "8"


def test_init_does_not_clobber_existing_state_on_a_second_call():
    """init() runs on every Streamlit rerun -- it must only *fill in*
    missing keys, never reset ones already set."""
    session_state.init()
    st.session_state.params = {"protein_id": "6lu7"}
    st.session_state.job_id = "job_x"
    st.session_state.param_protein_id = "6lu7"

    session_state.init()

    assert st.session_state.params == {"protein_id": "6lu7"}
    assert st.session_state.job_id == "job_x"
    assert st.session_state.param_protein_id == "6lu7"


# ---------------------------------------------------------------------------
# get_params
# ---------------------------------------------------------------------------

def test_get_params_returns_empty_dict_when_unset():
    assert session_state.get_params() == {}


def test_get_params_returns_current_params():
    st.session_state.params = {"protein_id": "6lu7"}
    assert session_state.get_params() == {"protein_id": "6lu7"}


# ---------------------------------------------------------------------------
# apply_parsed_params
# ---------------------------------------------------------------------------

def test_apply_parsed_params_updates_params_dict_and_widget_keys():
    st.session_state.params = {}
    session_state.apply_parsed_params(
        {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP", "ATP"],
            "center": [1.0, 2.0, 3.0],
            "box_size": [10.0, 20.0, 30.0],
            "ph": 7.4,
            "exhaustiveness": 8,
            "cpu": 2,
        }
    )

    assert st.session_state.params["protein_id"] == "6lu7"
    assert st.session_state.params["ligand_ids"] == ["ATP", "ATP"]
    assert st.session_state.param_protein_id == "6lu7"
    assert st.session_state.param_ligand_ids_text == "ATP, ATP"
    assert st.session_state.param_center_x == "1.0"
    assert st.session_state.param_center_y == "2.0"
    assert st.session_state.param_center_z == "3.0"
    assert st.session_state.param_box_x == "10.0"
    assert st.session_state.param_box_y == "20.0"
    assert st.session_state.param_box_z == "30.0"
    assert st.session_state.param_ph == "7.4"
    assert st.session_state.param_exhaustiveness == "8"
    assert st.session_state.param_cpu == "2"


def test_apply_parsed_params_ignores_none_values():
    st.session_state.params = {"protein_id": "6lu7"}
    session_state.apply_parsed_params({"protein_id": None, "ligand_ids": None})
    assert st.session_state.params == {"protein_id": "6lu7"}


def test_apply_parsed_params_skips_center_widget_sync_when_not_exactly_3_values():
    st.session_state.params = {}
    st.session_state.param_center_x = "unchanged"
    session_state.apply_parsed_params({"center": [1.0, 2.0]})
    assert st.session_state.param_center_x == "unchanged"
    # The raw value is still stored in params even though the widget mirror
    # was skipped -- apply_parsed_params() doesn't validate, only mirrors.
    assert st.session_state.params["center"] == [1.0, 2.0]


def test_apply_parsed_params_skips_box_widget_sync_when_not_exactly_3_values():
    st.session_state.params = {}
    st.session_state.param_box_x = "unchanged"
    session_state.apply_parsed_params({"box_size": [1.0, 2.0, 3.0, 4.0]})
    assert st.session_state.param_box_x == "unchanged"


# ---------------------------------------------------------------------------
# load_session_params
# ---------------------------------------------------------------------------

def test_load_session_params_replaces_params_entirely():
    st.session_state.params = {"protein_id": "old", "ligand_ids": ["OLD"]}
    session_state.load_session_params({"protein_id": "new"})
    assert st.session_state.params == {"protein_id": "new"}
    assert st.session_state.param_protein_id == "new"


# ---------------------------------------------------------------------------
# clear_box_picker_state
# ---------------------------------------------------------------------------

def test_clear_box_picker_state_removes_all_four_keys():
    st.session_state["_box_picker_receptor_pdb"] = "/x/y.pdb"
    st.session_state["picked_center"] = [1, 2, 3]
    st.session_state["_box_picker_last_size"] = [20, 20, 20]
    st.session_state["_pending_geometry_target"] = "center"

    session_state.clear_box_picker_state()

    assert "_box_picker_receptor_pdb" not in st.session_state
    assert "picked_center" not in st.session_state
    assert "_box_picker_last_size" not in st.session_state
    assert "_pending_geometry_target" not in st.session_state


def test_clear_box_picker_state_is_a_noop_when_nothing_to_clear():
    session_state.clear_box_picker_state()  # must not raise


# ---------------------------------------------------------------------------
# start_new_session -- Review Stage R1 (Phase 12): must also clear box
# picker state so a stale receptor can't leak into the new session.
# ---------------------------------------------------------------------------

def test_start_new_session_resets_job_identity_and_chat():
    session_state.init()
    st.session_state.job_id = "job_x"
    st.session_state.job_name = "trial A"
    st.session_state.viewed_job_id = "job_x"
    st.session_state.bridge = object()
    st.session_state["_job_done"] = True
    st.session_state["_pending_rerun"] = {"steps": ["dock"]}
    st.session_state.messages = [{"role": "user", "content": "hi"}]

    session_state.start_new_session()

    assert st.session_state.job_id is None
    assert st.session_state.job_name == ""
    assert st.session_state.viewed_job_id is None
    assert st.session_state.bridge is None
    assert st.session_state["_job_done"] is False
    assert "_pending_rerun" not in st.session_state
    assert st.session_state.messages == [dict(session_state._GREETING_MESSAGE)]
    assert isinstance(st.session_state.interactive_session, InteractiveSession)
    assert not st.session_state.interactive_session.has_active_job()


def test_start_new_session_clears_stale_box_picker_receptor():
    session_state.init()
    st.session_state["_box_picker_receptor_pdb"] = "/x/protein_a.pdb"
    st.session_state["picked_center"] = [1.0, 2.0, 3.0]

    session_state.start_new_session()

    assert "_box_picker_receptor_pdb" not in st.session_state
    assert "picked_center" not in st.session_state


def test_start_new_session_preserves_parameter_widgets():
    session_state.init()
    st.session_state.param_protein_id = "6lu7"
    st.session_state.param_center_x = "1.0"

    session_state.start_new_session()

    assert st.session_state.param_protein_id == "6lu7"
    assert st.session_state.param_center_x == "1.0"
