"""Tests for ui/core/chat.py -- hermetic, plain GuiSession,
fake LLM parse functions (no LLM client is ever called).

The NiceGUI page's own wiring over the same code is covered by
tests/test_nicegui_*.py (GUI test tier).
"""
import copy
from pathlib import Path

import pytest

from services.llm_orchestrator.workflow_parser import WorkflowParser
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from tests.ui_fakes import inline_run_save
from ui.core import chat
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap

FIXTURE_PDB = Path(__file__).parent / "fixtures" / "mini_protein.pdb"


def _boom(*_args, **_kwargs):
    raise AssertionError("the LLM must not be called for this message")


class FakeBridge:
    def start_workflow(self, *args, **kwargs):
        self.started_with = (args, kwargs)

    def is_running(self):
        return False


@pytest.fixture
def state():
    # A client is set, as with any configured provider; the fake parse
    # functions never call it. test_no_llm_* covers provider "none".
    s = GuiSession(worker_cap=WorkerCap(10, "compose"), llm_client="client")
    s.messages = []
    return s


def _contents(state):
    return [m["content"] for m in state.messages]


def _submit(state, message, output_base, run_docking=None):
    return chat.submit(
        state, message, output_base, FakeBridge,
        run_save=inline_run_save(state, output_base), run_docking=run_docking or _no_run,
    )


def _no_run():
    raise AssertionError("this message must not start docking")


# ---------------------------------------------------------------------------
# submit: deterministic commands never reach the LLM
# ---------------------------------------------------------------------------

def test_plain_message_is_posted_and_left_for_the_llm(state, tmp_path):
    assert _submit(state, "dock ATP into 6lu7", tmp_path) is False
    assert state.messages == [{"role": "user", "content": "dock ATP into 6lu7"}]


def test_no_llm_answers_free_text_with_the_setup_hint(state, tmp_path):
    # Provider "none" (the default) builds no client.
    state.llm_client = None

    assert _submit(state, "dock ATP into 6lu7", tmp_path) is True

    assert _contents(state) == ["dock ATP into 6lu7", chat.NO_LLM_REPLY]
    assert "Settings" in chat.NO_LLM_REPLY


def test_no_llm_still_runs_commands(state, tmp_path):
    state.llm_client = None
    runs = []

    assert _submit(state, "run", tmp_path, run_docking=lambda: runs.append(1)) is True

    assert runs == [1]
    assert chat.NO_LLM_REPLY not in _contents(state)


def test_geometry_command_uses_a_loaded_receptor(state, tmp_path):
    if not FIXTURE_PDB.exists():
        pytest.skip("Fixture file not available")
    receptor = tmp_path / "r.pdb"
    receptor.write_text(FIXTURE_PDB.read_text())
    state.box_picker_receptor_pdb = str(receptor)

    assert _submit(state, "look across all protein", tmp_path) is True

    assert "box_size" in state.params
    assert "cover the whole protein" in state.messages[-1]["content"]


def test_geometry_command_without_a_receptor_starts_the_prep_and_waits(state, tmp_path):
    state.form.update(input_mode="rcsb", param_protein_id="6lu7")

    assert _submit(state, "set box center as protein center", tmp_path) is True

    assert state.pending_geometry_targets == ("center",)
    assert isinstance(state.box_picker_bridge, FakeBridge)
    assert "fetching and preparing it now" in state.messages[-1]["content"]


def test_geometry_command_during_a_running_prep_waits_for_it(state, tmp_path):
    # A second prep must not start and replace the running one, whose
    # result would then never be processed.
    class RunningBridge(FakeBridge):
        def is_running(self):
            return True

    running = RunningBridge()
    state.box_picker_bridge = running

    chat.protein_geometry_command(state, "box_size", tmp_path, _boom)

    assert state.box_picker_bridge is running
    assert state.pending_geometry_targets == ("box_size",)
    assert "still preparing" in state.messages[-1]["content"]


class _RunningBridge(FakeBridge):
    def is_running(self):
        return True


def test_geometry_commands_during_a_prep_are_all_queued(state, tmp_path):
    # One slot kept only the last command,
    # though each had been told "I'll set this".
    state.box_picker_bridge = _RunningBridge()

    for target in ("center", "box_size", "center"):
        chat.protein_geometry_command(state, target, tmp_path, _boom)

    assert state.pending_geometry_targets == ("center", "box_size")


def test_geometry_command_during_a_prep_ignores_the_sessions_old_receptor(state, tmp_path):
    # Starting the prep clears the picker's
    # receptor, so the command fell back to the active session's last
    # prepared protein -- possibly another protein than the one being
    # prepared -- and applied its geometry at once.
    if not FIXTURE_PDB.exists():
        pytest.skip("Fixture file not available")
    old = tmp_path / "old_receptor.pdb"
    old.write_text(FIXTURE_PDB.read_text())
    state.interactive_session.last_results = {
        "results": {"prepare_protein": {"prepared_proteins": [{"pdb_path": str(old)}]}}
    }
    state.box_picker_bridge = _RunningBridge()

    chat.protein_geometry_command(state, "center", tmp_path, _boom)

    assert "center" not in state.params
    assert state.pending_geometry_targets == ("center",)
    assert "still preparing" in state.messages[-1]["content"]


def test_geometry_command_with_no_protein_source_says_what_is_missing(state, tmp_path):
    chat.protein_geometry_command(state, "center", tmp_path, lambda: (False, "Provide a protein source first — missing: protein_id", "warning"))
    assert state.pending_geometry_targets is None
    assert _contents(state) == ["Provide a protein source first — missing: protein_id"]


@pytest.mark.parametrize("active,hint", [(False, "It will be used"), (True, "redock with it")])
def test_algorithm_command_switches_the_selector(state, tmp_path, active, hint):
    if active:
        state.interactive_session.activate("job_x", WorkflowStateManager(tmp_path / "job_x"))

    assert _submit(state, "use vina", tmp_path) is True

    assert state.form["param_docking_algorithm"] == "vina"
    assert state.params["docking_algorithm"] == "vina"
    assert "Docking algorithm set to **Vina (Monte Carlo)**" in state.messages[-1]["content"]
    assert hint in state.messages[-1]["content"]


@pytest.mark.parametrize("message", ["run", "Start docking.", "redock it"])
def test_run_command_starts_docking(state, tmp_path, message):
    runs = []

    assert _submit(state, message, tmp_path, run_docking=lambda: runs.append(1)) is True

    assert runs == [1]
    assert state.messages == [{"role": "user", "content": message}]


def test_dock_with_algorithm_switches_then_runs(state, tmp_path):
    order = []
    run = lambda: order.append(state.form["param_docking_algorithm"])  # noqa: E731

    assert _submit(state, "dock with vina", tmp_path, run_docking=run) is True

    assert order == ["vina"]
    assert state.messages[-1]["content"] == "Docking algorithm set to **Vina (Monte Carlo)**."


def test_history_command_is_answered(state, tmp_path):
    assert _submit(state, "show history", tmp_path) is True
    assert state.messages[-1]["content"] == "No active session — no history yet."


@pytest.mark.parametrize("message,reply", [
    ("save results as run1", "Cannot save: No active session"),
    ("save results as", "Give the bundle a name"),
])
def test_save_command_is_answered(state, tmp_path, message, reply):
    assert _submit(state, message, tmp_path) is True
    assert state.messages[-1]["content"].startswith(reply)


# ---------------------------------------------------------------------------
# The LLM path
# ---------------------------------------------------------------------------

def test_first_message_is_a_full_parse(state):
    parsed = {"services": ["dock"], "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]}}
    result = chat.parse_message(
        "client", "dock ATP into 6lu7", None,
        parse_workflow=lambda client, msg: parsed, parse_followup=_boom,
    )
    assert result == (parsed, {"protein_id", "ligand_ids"})


def test_follow_up_merges_onto_the_prior_workflow_without_touching_it():
    prior = {"services": ["get_input_files", "dock"], "parameters": {"protein_id": "6lu7", "ph": 7.4}}
    prior_copy = copy.deepcopy(prior)
    seen = {}

    def followup(client, msg, *, current_parameters, current_services):
        seen.update(current_parameters=current_parameters, current_services=current_services)
        return {"services_add": ["dock", "visualize"], "parameters": {"ph": 6.5}}

    parsed, named = chat.parse_message("client", "pH 6.5", prior, parse_workflow=_boom, parse_followup=followup)

    assert parsed == {
        "services": ["get_input_files", "dock", "visualize"],
        "parameters": {"protein_id": "6lu7", "ph": 6.5},
    }
    assert named == {"ph"}
    assert seen == {"current_parameters": prior_copy["parameters"], "current_services": prior_copy["services"]}
    assert prior == prior_copy


def test_only_named_parameters_reach_the_form(state):
    # validate_workflow() fills the default "moldina" -- it must
    # not overwrite the selector's choice.
    state.form["param_docking_algorithm"] = "vina"
    state.form["param_ph"] = "6.0"

    chat.apply_parsed_message(state, {"services": ["dock"], "parameters": {"protein_id": "6lu7"}}, {"protein_id"})

    assert state.form["param_docking_algorithm"] == "vina"
    assert state.form["param_ph"] == "6.0"
    assert state.form["param_protein_id"] == "6lu7"
    reply = state.messages[-1]["content"]
    assert reply.startswith("Got it! Here's what I extracted:")
    assert "- Protein: **6lu7**" in reply
    assert "Algorithm" not in reply
    assert "Still needed:" in reply
    assert "Run Docking" not in reply


COMPLETE_PARAMS = {
    "protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [20.0, 20.0, 20.0],
}


@pytest.mark.parametrize("active,hint", [
    (False, "Say **run** or click **Run Docking** to start."),
    (True, "Say **redock** or click **Run Docking** to redock."),
])
def test_complete_parse_ends_with_the_run_hint(state, tmp_path, active, hint):
    if active:
        state.interactive_session.activate("job_x", WorkflowStateManager(tmp_path / "job_x"))

    chat.apply_parsed_message(state, {"services": ["dock"], "parameters": dict(COMPLETE_PARAMS)}, set(COMPLETE_PARAMS))

    reply = state.messages[-1]["content"]
    assert "Still needed:" not in reply
    assert reply.endswith(hint)


def test_named_algorithm_is_applied_and_echoed(state):
    chat.apply_parsed_message(
        state, {"services": ["dock"], "parameters": {"docking_algorithm": "PSO"}}, {"docking_algorithm"}
    )
    assert state.form["param_docking_algorithm"] == "moldina"
    assert "- Algorithm: **Moldina (PSO)**" in state.messages[-1]["content"]


def test_unknown_algorithm_keeps_the_selection_and_says_so(state):
    state.form["param_docking_algorithm"] = "vina"

    chat.apply_parsed_message(
        state, {"services": ["dock"], "parameters": {"docking_algorithm": "genetic"}}, {"docking_algorithm"}
    )

    assert state.form["param_docking_algorithm"] == "vina"
    assert state.parser.parsed_workflow["parameters"]["docking_algorithm"] == "vina"
    reply = state.messages[-1]["content"]
    assert "Docking algorithm 'genetic' is not recognised, so Vina (Monte Carlo) stays selected." in reply
    assert "docking_algorithm" not in reply.split("Still needed:")[-1]


def _answer(state, message, *, parse_workflow, parse_followup):
    """The GUI's LLM path, run inline: parse, apply, any failure as the reply."""
    try:
        parsed, named = chat.parse_message(
            state.llm_client, message, state.parser.parsed_workflow,
            parse_workflow=parse_workflow, parse_followup=parse_followup,
        )
        chat.apply_parsed_message(state, parsed, named)
    except Exception as exc:
        chat.parse_failed(state, exc)


def test_a_parse_seeds_the_follow_up(state):
    state.llm_client = "client"
    _answer(
        state, "dock 6lu7",
        parse_workflow=lambda c, m: {"services": ["dock"], "parameters": {"protein_id": "6lu7"}},
        parse_followup=_boom,
    )
    assert state.params["protein_id"] == "6lu7"

    _answer(
        state, "pH 6.5", parse_workflow=_boom,
        parse_followup=lambda c, m, **k: {"services_add": [], "parameters": {"ph": 6.5}},
    )
    assert state.params["ph"] == 6.5
    assert state.form["param_protein_id"] == "6lu7"


def test_a_parse_failure_is_the_reply(state):
    def fail(*_a, **_k):
        raise RuntimeError("LLM offline")

    _answer(state, "dock 6lu7", parse_workflow=fail, parse_followup=_boom)

    assert _contents(state) == ["Sorry, I couldn't parse that: LLM offline"]
    assert state.params == {}


def test_a_missing_parser_is_the_reply(state):
    state.parser = None
    _answer(state, "dock 6lu7", parse_workflow=_boom, parse_followup=_boom)
    assert state.messages[-1]["content"].startswith("Sorry, I couldn't parse that:")


# ---------------------------------------------------------------------------
# A parse that ran off the event loop is applied only to the
# session it was built on
# ---------------------------------------------------------------------------

_PARSED = {"services": ["dock"], "parameters": {"protein_id": "1abc"}}


def test_parse_applied_when_the_session_is_unchanged(state):
    context = chat.parse_context(state)
    chat.apply_parsed_message(state, copy.deepcopy(_PARSED), {"protein_id"}, context=context)
    assert state.form["param_protein_id"] == "1abc"


def test_parse_dropped_when_the_parser_moved_on(state):
    # e.g. a Load or `undo` re-seeded the parser while the LLM call ran.
    context = chat.parse_context(state)
    state.parser.validate_workflow({"services": ["dock"], "parameters": {"protein_id": "6lu7"}})
    seeded = state.parser.parsed_workflow

    chat.apply_parsed_message(state, copy.deepcopy(_PARSED), {"protein_id"}, context=context)

    assert state.parser.parsed_workflow is seeded
    assert state.form["param_protein_id"] == ""
    assert _contents(state) == [chat.STALE_PARSE_REPLY]


def test_parse_dropped_after_loading_a_session_without_saved_parameters(state, tmp_path):
    # Load re-activates the same InteractiveSession and, with no draft or
    # checkpoint on disk, leaves the parser's workflow as it was: only the
    # job id shows the change.
    from ui.core.sessions import resume_session

    job_id = "job_20261012_000000_abcdef"
    context = chat.parse_context(state)
    resume_session(state, {"job_id": job_id, "job_dir": str(tmp_path / job_id)}, tmp_path)

    chat.apply_parsed_message(state, copy.deepcopy(_PARSED), {"protein_id"}, context=context)

    assert state.form["param_protein_id"] == ""
    assert _contents(state)[-1] == chat.STALE_PARSE_REPLY


def test_parse_dropped_after_a_new_session(state):
    # New Session replaces the interactive session but not the parser.
    from ui.core.form import start_new_session

    context = chat.parse_context(state)
    start_new_session(state)

    chat.apply_parsed_message(state, copy.deepcopy(_PARSED), {"protein_id"}, context=context)

    assert state.params == {}
    assert _contents(state)[-1] == chat.STALE_PARSE_REPLY
