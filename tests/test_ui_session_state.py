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
from ui.worker_cap import MEASURED_BEST_WORKERS


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


def test_load_session_params_resets_widgets_the_loaded_params_lack():
    # e.g. restoring a protein_file revision over an RCSB one: without the
    # reset, the stale protein_id widget would survive and be docked.
    session_state.init()
    st.session_state.param_protein_id = "6lu7"
    st.session_state.param_ligand_ids_text = "ATP"
    st.session_state.param_center_x = "9.0"
    st.session_state.param_job_name = "my run"

    session_state.load_session_params({"protein_file": "/x/receptor.pdb", "ligand_files": ["/x/l.sdf"]})

    assert st.session_state.param_protein_id == ""
    assert st.session_state.param_ligand_ids_text == ""
    assert st.session_state.param_center_x == ""
    # Names the *next* new session only -- not part of a loaded revision.
    assert st.session_state.param_job_name == "my run"


def test_max_workers_default_is_measured_best_clamped_to_cap():
    session_state.init()
    assert st.session_state.param_max_workers == min(
        MEASURED_BEST_WORKERS["moldina"], session_state.WORKER_CAP.cap
    )


def test_load_session_params_sets_parallel_widgets_clamped():
    session_state.init()
    session_state.load_session_params(
        {"protein_id": "6lu7", "parallel": True, "max_workers": session_state.WORKER_CAP.cap + 50}
    )
    assert st.session_state.param_parallel is True
    assert st.session_state.param_max_workers == session_state.WORKER_CAP.cap


# ---------------------------------------------------------------------------
# Docking algorithm selector (Stage 15.3)
# ---------------------------------------------------------------------------

@pytest.fixture
def cap10(monkeypatch):
    from ui.worker_cap import WorkerCap
    monkeypatch.setattr(session_state, "WORKER_CAP", WorkerCap(10, "compose"))


def test_init_selects_moldina():
    session_state.init()
    assert st.session_state.param_docking_algorithm == "moldina"


@pytest.mark.parametrize("algorithm, cpu, expected", [
    ("moldina", "4", 1),  # PSO ignores --cpu: always 1
    ("vina", "4", 4),
    ("vina", "", 1),
    ("vina", "abc", 1),
    ("vina", "0", 1),
])
def test_effective_cpu(algorithm, cpu, expected):
    st.session_state["param_docking_algorithm"] = algorithm
    st.session_state["param_cpu"] = cpu
    assert session_state.effective_cpu() == expected


def test_switching_algorithm_resets_workers_to_its_best(cap10):
    session_state.init()
    st.session_state.param_max_workers = 3

    session_state.set_docking_algorithm_widget("vina")

    assert st.session_state.param_docking_algorithm == "vina"
    assert st.session_state.param_max_workers == min(MEASURED_BEST_WORKERS["vina"], 10)


def test_switch_at_several_cpus_per_ligand_stays_within_quota(cap10):
    session_state.init()
    st.session_state.param_cpu = "4"

    session_state.set_docking_algorithm_widget("vina")

    assert st.session_state.param_max_workers == min(MEASURED_BEST_WORKERS["vina"], 10 // 4)


def test_selecting_the_current_algorithm_keeps_workers(cap10):
    session_state.init()
    st.session_state.param_max_workers = 3

    session_state.set_docking_algorithm_widget("moldina")

    assert st.session_state.param_max_workers == 3


def test_apply_parsed_params_without_algorithm_keeps_the_selector():
    # Phase 15 finding 8: a chat parse that didn't name the algorithm must
    # not reset a chosen one.
    session_state.init()
    st.session_state.param_docking_algorithm = "vina"

    session_state.apply_parsed_params({"protein_id": "6lu7", "center": [1, 2, 3]})

    assert st.session_state.param_docking_algorithm == "vina"


def test_apply_parsed_params_with_algorithm_moves_the_selector():
    session_state.init()
    session_state.apply_parsed_params({"docking_algorithm": "vina"})
    assert st.session_state.param_docking_algorithm == "vina"
    assert st.session_state.params["docking_algorithm"] == "vina"


@pytest.mark.parametrize("value", ["gnina", "", None, 3])
def test_apply_parsed_params_ignores_an_unrecognised_algorithm(value):
    # The LLM path passes an explicit-but-invalid name through (the
    # validator lists it as missing); st.radio raises on a value outside
    # its options.
    session_state.init()
    st.session_state.param_docking_algorithm = "vina"

    session_state.apply_parsed_params({"docking_algorithm": value})

    assert st.session_state.param_docking_algorithm == "vina"


def test_load_of_session_without_algorithm_reads_as_moldina():
    # Phase 15 finding 9: sessions saved before Phase 15 have no key.
    session_state.init()
    st.session_state.param_docking_algorithm = "vina"

    session_state.load_session_params({"protein_id": "6lu7", "parallel": True, "max_workers": 3})

    assert st.session_state.param_docking_algorithm == "moldina"
    assert st.session_state.param_max_workers == 3


def test_load_of_vina_session_keeps_its_saved_workers(cap10):
    session_state.init()

    session_state.load_session_params(
        {"protein_id": "6lu7", "docking_algorithm": "vina", "cpu": 2, "parallel": True, "max_workers": 3}
    )

    assert st.session_state.param_docking_algorithm == "vina"
    assert st.session_state.param_cpu == "2"
    assert st.session_state.param_max_workers == 3


def test_load_clamps_workers_to_cap_divided_by_cpu(cap10):
    session_state.init()

    session_state.load_session_params(
        {"protein_id": "6lu7", "docking_algorithm": "vina", "cpu": 4, "parallel": True, "max_workers": 10}
    )

    assert st.session_state.param_max_workers == 2


# ---------------------------------------------------------------------------
# clear_box_picker_state
# ---------------------------------------------------------------------------

def test_clear_box_picker_state_removes_picker_keys_but_not_the_box():
    st.session_state["_box_picker_receptor_pdb"] = "/x/y.pdb"
    st.session_state["_box_picker_last_click"] = {"center": [1, 2, 3], "clickId": 1}
    st.session_state["_pending_geometry_targets"] = ("center",)
    st.session_state["_box_picker_error"] = "boom"
    st.session_state["param_center_x"] = "1.0"

    session_state.clear_box_picker_state()

    for key in ("_box_picker_receptor_pdb", "_box_picker_last_click",
                "_pending_geometry_targets", "_box_picker_error"):
        assert key not in st.session_state
    # The Docking Box fields are the box itself, not picker state.
    assert st.session_state["param_center_x"] == "1.0"


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
    st.session_state["_box_picker_last_click"] = {"center": [1.0, 2.0, 3.0], "clickId": 1}

    session_state.start_new_session()

    assert "_box_picker_receptor_pdb" not in st.session_state
    assert "_box_picker_last_click" not in st.session_state


def test_start_new_session_preserves_parameter_widgets():
    session_state.init()
    st.session_state.param_protein_id = "6lu7"
    st.session_state.param_center_x = "1.0"

    session_state.start_new_session()

    assert st.session_state.param_protein_id == "6lu7"
    assert st.session_state.param_center_x == "1.0"
