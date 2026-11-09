"""Tests for ui/core/state.py and ui/core/form.py.

Framework-free: these run in the hermetic suite (no nicegui), against a
plain GuiSession. The NiceGUI page's own behaviour over the same code is
covered by tests/test_nicegui_*.py (GUI test tier).
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from services.llm_orchestrator.interactive_session import InteractiveSession
from ui.core import form
from ui.core.state import GREETING_MESSAGE, GuiSession, Outcome, form_defaults
from ui.worker_cap import MEASURED_BEST_WORKERS, WorkerCap

CAP10 = WorkerCap(10, "compose")
CORE_DIR = Path(__file__).resolve().parents[1] / "ui" / "core"


@pytest.fixture
def state():
    return GuiSession(worker_cap=CAP10)


# ---------------------------------------------------------------------------
# GuiSession
# ---------------------------------------------------------------------------

def test_new_session_has_greeting_empty_params_and_default_form(state):
    assert state.messages == [GREETING_MESSAGE]
    assert state.messages[0] is not GREETING_MESSAGE  # a copy, not the shared dict
    assert state.params == {}
    assert state.form == {**form_defaults(CAP10), "input_mode": "local_files"}
    assert state.form["param_max_workers"] == min(MEASURED_BEST_WORKERS["moldina"], 10)
    assert state.form["param_docking_algorithm"] == "moldina"
    assert state.job_id is None and state.bridge is None and not state.job_done
    assert not state.interactive_session.has_active_job()


def test_sessions_share_no_mutable_state():
    a, b = GuiSession(worker_cap=CAP10), GuiSession(worker_cap=CAP10)
    a.messages.append({"role": "user", "content": "hi"})
    a.form["param_protein_id"] = "6lu7"
    a.params["x"] = 1
    assert b.messages == [GREETING_MESSAGE]
    assert b.form["param_protein_id"] == ""
    assert b.params == {}
    assert a.interactive_session is not b.interactive_session


def test_given_form_values_are_kept_and_the_rest_filled_in():
    state = GuiSession(worker_cap=CAP10, form={"param_ph": "6.5", "input_mode": "rcsb"})
    assert state.form["param_ph"] == "6.5"
    assert state.form["input_mode"] == "rcsb"
    assert state.form["param_exhaustiveness"] == "8"


def test_outcome_defaults_to_nothing_to_do():
    assert Outcome() == Outcome(error=None)


def test_no_ui_core_module_imports_a_gui_framework():
    """ui/core must stay importable without streamlit or nicegui."""
    for path in CORE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in ("streamlit", "nicegui"), f"{path.name} imports {name}"
                assert name.split(".")[0] != "ui_nice", f"{path.name} imports {name}"


def test_importing_ui_core_loads_no_gui_framework_indirectly():
    """The check above sees only ui/core's own imports. This one also
    catches a framework pulled in through a module ui/core imports (e.g.
    ui.job_history, ui.worker_cap), and works in the ui image, where
    nicegui is installed."""
    modules = ", ".join(f"ui.core.{p.stem}" for p in sorted(CORE_DIR.glob("*.py")) if p.stem != "__init__")
    code = (
        f"import sys, {modules}\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('streamlit', 'nicegui', 'ui_nice'))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=CORE_DIR.parents[1], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Algorithm, CPUs per ligand, workers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "algorithm, cpu, expected",
    [("moldina", 4, 1), ("vina", 4, 4), ("vina", "4", 4), ("vina", "4.0", 4), ("vina", "2.5", 2),
     ("vina", "all", 1), ("vina", "", 1), ("vina", None, 1), ("vina", "0", 1)],
)
def test_effective_cpu(state, algorithm, cpu, expected):
    state.form["param_docking_algorithm"] = algorithm
    state.form["param_cpu"] = cpu
    assert form.effective_cpu(state) == expected


@pytest.mark.parametrize("cpu", [4.0, "all"])
def test_a_chat_cpu_that_is_not_a_whole_number_does_not_break_the_form(state, cpu):
    # The chat writes the LLM's cpu as a string, and once
    # 16.8 dropped effective_cpu's fallback, "4.0" or "all" raised in every
    # redraw (max_workers) and in Run Docking (collect_form_params).
    state.form["param_docking_algorithm"] = "vina"
    form.apply_parsed_params(state, {"cpu": cpu})

    assert form.max_workers(state) >= 1
    assert form.effective_cpu(state) == (4 if cpu == 4.0 else 1)


def test_switching_algorithm_resets_workers_to_its_best(state):
    state.form["param_max_workers"] = 3
    form.set_docking_algorithm(state, "vina")
    assert state.form["param_docking_algorithm"] == "vina"
    assert state.form["param_max_workers"] == min(MEASURED_BEST_WORKERS["vina"], 10)


def test_switch_at_several_cpus_per_ligand_stays_within_quota(state):
    state.form["param_cpu"] = "4"
    form.set_docking_algorithm(state, "vina")
    assert state.form["param_max_workers"] * 4 <= 10


def test_selecting_the_current_algorithm_keeps_workers(state):
    state.form["param_max_workers"] = 3
    form.set_docking_algorithm(state, "moldina")
    assert state.form["param_max_workers"] == 3


# ---------------------------------------------------------------------------
# apply_parsed_params / load_session_params
# ---------------------------------------------------------------------------

def test_apply_parsed_params_writes_params_and_form(state):
    form.apply_parsed_params(
        state,
        {"protein_id": "6lu7", "ligand_ids": ["ATP", "HEM"], "center": [1.0, 2.0, 3.0],
         "box_size": [10.0, 20.0, 30.0], "ph": 7.0, "cpu": 2, "ligand_folder": None},
    )
    assert state.params == {
        "protein_id": "6lu7", "ligand_ids": ["ATP", "HEM"], "center": [1.0, 2.0, 3.0],
        "box_size": [10.0, 20.0, 30.0], "ph": 7.0, "cpu": 2,
    }
    assert state.form["param_protein_id"] == "6lu7"
    assert state.form["param_ligand_ids_text"] == "ATP, HEM"
    assert [state.form[k] for k in form.BOX_FORM_KEYS] == ["1.0", "2.0", "3.0", "10.0", "20.0", "30.0"]
    assert state.form["param_ph"] == "7.0"
    assert state.form["param_cpu"] == 2


def test_apply_parsed_params_keeps_values_parsed_as_none(state):
    state.params = {"protein_id": "6lu7"}
    form.apply_parsed_params(state, {"protein_id": None, "ligand_ids": None})
    assert state.params == {"protein_id": "6lu7"}


@pytest.mark.parametrize("key, value, field", [
    ("center", [1.0, 2.0], "param_center_x"),
    ("box_size", [1.0, 2.0, 3.0, 4.0], "param_box_x"),
])
def test_apply_parsed_params_skips_a_box_field_group_without_three_values(state, key, value, field):
    state.form[field] = "unchanged"
    form.apply_parsed_params(state, {key: value})
    assert state.form[field] == "unchanged"
    # The raw value is still stored: apply_parsed_params() mirrors, it
    # doesn't validate.
    assert state.params[key] == value


def test_apply_parsed_params_without_algorithm_keeps_the_selector(state):
    # A chat parse that didn't name the algorithm must
    # not reset a chosen one.
    state.form["param_docking_algorithm"] = "vina"
    form.apply_parsed_params(state, {"protein_id": "6lu7", "center": [1, 2, 3]})
    assert state.form["param_docking_algorithm"] == "vina"


@pytest.mark.parametrize("value", ["gnina", "", None])
def test_apply_parsed_params_ignores_an_unrecognised_algorithm(state, value):
    form.apply_parsed_params(state, {"docking_algorithm": value})
    assert state.form["param_docking_algorithm"] == "moldina"


def test_apply_parsed_params_with_algorithm_moves_the_selector(state):
    form.apply_parsed_params(state, {"docking_algorithm": "vina"})
    assert state.form["param_docking_algorithm"] == "vina"


def test_load_session_params_resets_fields_the_loaded_params_lack(state):
    state.form.update(param_protein_id="6lu7", param_center_x="9.0", param_job_name="my run")
    state.params = {"protein_id": "6lu7", "ligand_ids": ["ATP"]}

    form.load_session_params(state, {"protein_file": "/x/receptor.pdb", "ligand_files": ["/x/l.sdf"]})

    assert state.params == {"protein_file": "/x/receptor.pdb", "ligand_files": ["/x/l.sdf"]}
    assert state.form["param_protein_id"] == ""
    assert state.form["param_center_x"] == ""
    assert state.form["param_job_name"] == "my run"  # names the next new session only


def test_load_session_params_clamps_workers_to_cap_divided_by_cpu(state):
    form.load_session_params(
        state, {"docking_algorithm": "vina", "cpu": 4, "parallel": True, "max_workers": 9}
    )
    assert state.form["param_parallel"] is True
    assert state.form["param_max_workers"] == 10 // 4


def test_load_of_vina_session_keeps_its_saved_workers(state):
    form.load_session_params(
        state, {"protein_id": "6lu7", "docking_algorithm": "vina", "cpu": 2, "parallel": True, "max_workers": 3}
    )
    assert state.form["param_docking_algorithm"] == "vina"
    assert state.form["param_cpu"] == 2
    assert state.form["param_max_workers"] == 3


def test_load_of_session_without_algorithm_reads_as_moldina(state):
    state.form["param_docking_algorithm"] = "vina"
    form.load_session_params(state, {"protein_id": "6lu7", "max_workers": 3})
    assert state.form["param_docking_algorithm"] == "moldina"
    assert state.form["param_max_workers"] == 3


# ---------------------------------------------------------------------------
# Form -> params
# ---------------------------------------------------------------------------

def test_collect_form_params_rcsb_mode(state):
    state.form.update(
        input_mode="rcsb", param_protein_id=" 6lu7 ", param_ligand_ids_text="ATP, ,HEM",
        param_center_x="1", param_center_y="2", param_center_z="3",
        param_box_x="20", param_box_y="20", param_box_z="20",
    )
    params = form.collect_form_params(state)
    assert params["protein_id"] == "6lu7"
    assert params["ligand_ids"] == ["ATP", "HEM"]
    assert params["center"] == [1.0, 2.0, 3.0]
    assert params["box_size"] == [20.0, 20.0, 20.0]
    assert params["ph"] == 7.4 and params["exhaustiveness"] == 8
    assert params["docking_algorithm"] == "moldina" and params["cpu"] == 1
    assert params["parallel"] is False


def test_collect_form_params_omits_incomplete_box_and_clamps_workers(state):
    state.form.update(param_center_x="1", param_center_y="", param_max_workers=0)
    params = form.collect_form_params(state)
    assert "center" not in params and "box_size" not in params
    assert params["max_workers"] == 1


def test_collect_form_params_folder_mode_skips_ligand_text_field(state):
    state.form.update(input_mode="folder", param_protein_id="6lu7", param_ligand_ids_text="ATP")
    params = form.collect_form_params(state)
    assert params["protein_id"] == "6lu7"
    assert "ligand_ids" not in params


def test_sync_form_into_params_keeps_upload_paths(state):
    state.params = {"protein_file": "/x/r.pdb"}
    copy = form.sync_form_into_params(state)
    assert copy["protein_file"] == "/x/r.pdb" and "ph" in copy
    copy["ph"] = 0
    assert state.params["ph"] == 7.4  # a copy


# ---------------------------------------------------------------------------
# Box picker state / new session
# ---------------------------------------------------------------------------

def test_clear_box_picker_state_keeps_the_box(state):
    state.box_picker_receptor_pdb = "/x/y.pdb"
    state.pending_geometry_targets = ("center",)
    state.box_picker_error = "boom"
    state.form["param_center_x"] = "1.0"

    form.clear_box_picker_state(state)

    assert (state.box_picker_receptor_pdb, state.pending_geometry_targets,
            state.box_picker_error) == (None, None, None)
    assert state.form["param_center_x"] == "1.0"


def test_clear_box_picker_state_is_a_noop_when_nothing_to_clear(state):
    form.clear_box_picker_state(state)
    assert state.box_picker_receptor_pdb is None and state.pending_geometry_targets is None


def test_start_new_session_detaches_but_keeps_the_form(state):
    old_session = state.interactive_session
    state.job_id = state.viewed_job_id = "job_x"
    state.job_name = "trial"
    state.bridge = object()
    state.job_done = True
    state.pending_rerun = {"steps": ["dock"]}
    state.messages.append({"role": "user", "content": "hi"})
    state.box_picker_receptor_pdb = "/x/a.pdb"
    state.form["param_protein_id"] = "6lu7"
    state.run_log.append("a line")

    form.start_new_session(state)

    assert state.interactive_session is not old_session
    assert isinstance(state.interactive_session, InteractiveSession)
    assert (state.job_id, state.viewed_job_id, state.job_name, state.bridge) == (None, None, "", None)
    assert state.job_done is False and state.pending_rerun is None
    assert state.messages == [GREETING_MESSAGE]
    assert state.box_picker_receptor_pdb is None
    assert state.run_log == []
    assert state.form["param_protein_id"] == "6lu7"


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------

def test_reset_session_restores_a_fresh_session_but_keeps_llm_settings(state):
    state.settings = {"provider": "ollama", "api_key": "typed-this-session"}
    state.llm_client = client = object()
    state.job_id = "job_x"
    state.params["protein_id"] = "6lu7"
    state.form["param_center_x"] = "1.0"
    state.form["input_mode"] = "rcsb"
    state.messages.append({"role": "user", "content": "hi"})
    form_dict = state.form

    form.reset_session(state)

    fresh = GuiSession(worker_cap=CAP10)
    assert state.job_id is None and state.params == {}
    assert state.form == fresh.form and state.form is not form_dict
    assert state.messages == [GREETING_MESSAGE]
    assert state.settings == {"provider": "ollama", "api_key": "typed-this-session"}
    assert state.llm_client is client
    assert state.worker_cap is CAP10


@pytest.mark.parametrize("commit, key, text, params_key, expected, message", [
    (form.commit_protein_id, "param_protein_id", " 6lu7 ", "protein_id", "6lu7", "Updated protein ID: **6lu7**"),
    (form.commit_ligand_ids, "param_ligand_ids_text", "ATP, ,HEM", "ligand_ids", ["ATP", "HEM"],
     "Updated ligands: **ATP, HEM**"),
])
def test_committing_an_id_field_replies_only_when_it_changed(state, commit, key, text, params_key, expected, message):
    state.form[key] = text

    commit(state)
    commit(state)

    assert state.params[params_key] == expected
    assert [m["content"] for m in state.messages].count(message) == 1


def test_committing_an_empty_id_field_changes_nothing(state):
    form.commit_protein_id(state)
    form.commit_ligand_ids(state)
    assert state.params == {} and state.messages == [GREETING_MESSAGE]


def test_params_summary_is_none_until_there_is_something_to_show(state):
    assert form.params_summary(state) is None
    state.form["param_center_x"] = "1.0"
    assert form.params_summary(state) is not None


def test_params_summary_reads_the_box_fields_and_the_selector(state):
    state.params = {"protein_file": "/up/receptor.pdb", "ligand_files": ["/up/a.sdf", "/up/b.mol2"],
                    "center": [9.0, 9.0, 9.0]}
    state.form.update(param_center_x="1.0", param_box_z="30", param_docking_algorithm="vina")

    summary = form.params_summary(state)

    assert "**Protein:** receptor.pdb" in summary
    assert "**Ligands:** a.sdf, b.mol2" in summary
    assert "**Center:** 1.0 / — / —" in summary
    assert "**Box:** — / — / 30" in summary
    assert "**Algorithm:** Vina (Monte Carlo)" in summary
    assert "9.0" not in summary


@pytest.mark.parametrize("algorithm, cpu, workers, expected_max, expected_workers", [
    ("vina", "4", 10, 2, 2),       # clamped to 10 CPUs // 4
    ("vina", "4", 1, 2, 1),        # below the max: kept
    ("moldina", "4", 10, 10, 10),  # PSO docks with 1 CPU whatever the field says
])
def test_max_workers_bounds_and_clamps_the_field(state, algorithm, cpu, workers, expected_max, expected_workers):
    state.form.update(param_docking_algorithm=algorithm, param_cpu=cpu, param_max_workers=workers)
    assert form.max_workers(state) == expected_max
    assert state.form["param_max_workers"] == expected_workers
