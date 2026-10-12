"""The NiceGUI chat drawer (Phase 16, Stage 16.4): commands, the LLM parse
off the event loop, and the run log.

Ports the chat rows of the parity table (tests/test_nicegui_parity.py)
from tests/test_ui_apptest.py; each ported test keeps its AppTest name.
Runs in the ui-nice tier only (see tests/test_nicegui_setup.py).

The LLM is never called: tests patch the parse functions on
``services.llm_orchestrator.workflow_llm_parser``, which the drawer looks
up at call time.
"""
import json
import threading

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing import User  # noqa: E402

from ui.core.chat import STALE_PARSE_REPLY  # noqa: E402
from ui.worker_cap import MEASURED_BEST_WORKERS, WorkerCap  # noqa: E402
from tests.nicegui_harness import (  # noqa: E402
    HISTORY_JOB_ID,
    active_session_with_two_revisions,
    click,
    completed_session_with_artifacts,
    element,
    eventually,
    open_advanced,
    send_chat,
    set_value,
    with_worker_cap,
)
from tests.ui_fakes import RunningBridge  # noqa: E402

PARSER = "services.llm_orchestrator.workflow_llm_parser"


def _parse_returning(parameters: dict):
    return lambda client, message: {"services": ["dock"], "parameters": dict(parameters)}


def _bundle_meta(job_dir, name):
    return json.loads((job_dir / "saved_results" / name / "metadata.json").read_text())


def _running(page, output_base):
    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.bridge = RunningBridge()

    page.seed(seed)


# ---------------------------------------------------------------------------
# Revision history and undo (Stage 13.2)
# ---------------------------------------------------------------------------

async def test_chat_history_without_active_session(user: User, page):
    await user.open("/")

    await send_chat(user, page, "show history")

    assert page.chat_texts()[-1] == "No active session — no history yet."
    await user.should_see("No active session")


async def test_chat_show_history_and_undo(user: User, page, output_base):
    page.seed(lambda state: active_session_with_two_revisions(state, output_base))
    await user.open("/")
    state_manager = page.state.interactive_session.state_manager

    await send_chat(user, page, "show history")
    reply = page.chat_texts()[-1]
    assert "| 2 |" in reply and "currently at revision **2**" in reply

    await send_chat(user, page, "undo")
    assert "Which revision?" in page.chat_texts()[-1]

    await send_chat(user, page, "undo 99")
    assert page.chat_texts()[-1].startswith("Cannot restore revision 99")
    assert state_manager.get_parameters_revision() == 2

    await send_chat(user, page, "undo 1")
    assert page.chat_texts()[-1].startswith("Restored revision 1.")
    assert page.state.form["param_center_x"] == "1.0"
    # The form shows the restored revision at once.
    assert element(user, "param_center_x").value == "1.0"
    assert state_manager.load_latest_parameters()["center"] == [1.0, 2.0, 3.0]
    assert page.state.interactive_session.pending_changed_params


async def test_chat_undo_refused_while_job_runs(user: User, page, output_base):
    _running(page, output_base)
    await user.open("/")

    await send_chat(user, page, "undo 1")

    assert "A session is running — Stop it before restoring a revision." in page.chat_texts()
    assert page.state.interactive_session.state_manager.get_parameters_revision() == 2


async def test_chat_undo_of_state_only_revision_is_refused(user: User, page, output_base):
    revisions = {}

    def seed(state):
        state_manager = active_session_with_two_revisions(state, output_base)
        revisions["state"] = state_manager.save_execution_state_checkpoint({"protein_file": "/x/a.pdbqt"}, step="dock")
        state_manager.save_execution_state_checkpoint({"protein_file": "/x/b.pdbqt"}, step="visualize")

    page.seed(seed)
    await user.open("/")
    state_manager = page.state.interactive_session.state_manager

    await send_chat(user, page, f"undo {revisions['state']}")

    assert "has no parameter checkpoint" in page.chat_texts()[-1]
    assert state_manager.load_latest_state()["protein_file"] == "/x/b.pdbqt"
    assert state_manager.get_parameters_revision() == 2


# ---------------------------------------------------------------------------
# Save results as (Stage 13.3)
# ---------------------------------------------------------------------------

async def test_chat_save_refused_while_job_runs(user: User, page, output_base):
    def seed(state):
        completed_session_with_artifacts(state, output_base)
        state.bridge = RunningBridge()

    page.seed(seed)
    await user.open("/")

    await send_chat(user, page, "save results as trial_a")

    assert page.chat_texts()[-1].startswith("Cannot save: A session is running")
    assert not (output_base / HISTORY_JOB_ID / "saved_results").exists()


async def test_chat_save_docking_as_name(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await user.open("/")
    job_dir = output_base / HISTORY_JOB_ID

    await send_chat(user, page, "save docking as dock_only")

    assert _bundle_meta(job_dir, "dock_only")["scope"] == "docking"
    assert not (job_dir / "saved_results" / "dock_only" / "visualization").exists()

    await send_chat(user, page, "save results")
    assert page.chat_texts()[-1] == "Give the bundle a name, e.g. `save results as run1`."


# ---------------------------------------------------------------------------
# Docking algorithm (Stage 15.3)
# ---------------------------------------------------------------------------

async def test_chat_parse_without_algorithm_keeps_the_selector(user: User, page, monkeypatch):
    monkeypatch.setattr(f"{PARSER}.parse_workflow_with_llm", _parse_returning({"protein_id": "6lu7"}))
    await user.open("/")
    open_advanced(user)
    set_value(user, "param_docking_algorithm", "vina")

    await send_chat(user, page, "dock 6lu7")

    assert element(user, "param_docking_algorithm").value == "vina"
    assert page.chat_texts()[-1].startswith("Got it!")


async def test_chat_parse_naming_an_unknown_algorithm_renders_without_crash(user: User, page, monkeypatch):
    monkeypatch.setattr(
        f"{PARSER}.parse_workflow_with_llm",
        _parse_returning({"protein_id": "6lu7", "docking_algorithm": "gnina"}),
    )
    await user.open("/")

    await send_chat(user, page, "dock 6lu7 with gnina")

    assert element(user, "param_docking_algorithm").value == "moldina"
    reply = page.chat_texts()[-1]
    assert "'gnina' is not recognised, so Moldina (PSO) stays selected" in reply
    assert "docking_algorithm" not in reply

    # Review Stage R1, Phase 15, correctness #6: the bad name used to ride
    # along into every follow-up merge and be asked about again each time.
    monkeypatch.setattr(
        f"{PARSER}.parse_followup_with_llm",
        lambda client, message, **_kw: {"services_add": [], "parameters": {"ph": 6.5}},
    )
    await send_chat(user, page, "set pH to 6.5")

    assert "not recognised" not in page.chat_texts()[-1]
    assert "docking_algorithm" not in page.chat_texts()[-1]


async def test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph(user: User, page, monkeypatch):
    """Review Stage R1, Phase 15, correctness #3: only what the message
    named reaches the form, not the validator's defaults."""
    monkeypatch.setattr(f"{PARSER}.parse_workflow_with_llm", _parse_returning({"center": [10.0, 20.0, 30.0]}))
    with_worker_cap(page, WorkerCap(10, "compose"))
    await user.open("/")
    open_advanced(user)
    set_value(user, "param_docking_algorithm", "vina")
    set_value(user, "param_cpu", 2)
    set_value(user, "param_exhaustiveness", "16")
    set_value(user, "param_ph", "6.5")

    await send_chat(user, page, "set center to 10, 20, 30")

    assert element(user, "param_center_x").value == "10.0"
    assert element(user, "param_cpu").value == 2
    assert element(user, "param_exhaustiveness").value == "16"
    assert element(user, "param_ph").value == "6.5"


async def test_chat_algorithm_command_moves_the_selector_and_resets_workers(user: User, page):
    with_worker_cap(page, WorkerCap(10, "compose"))
    await user.open("/")
    open_advanced(user)
    set_value(user, "param_docking_algorithm", "vina")
    # The workers field is disabled until Parallel is ticked, as for a user.
    set_value(user, "param_parallel", True)
    set_value(user, "param_max_workers", 3)

    await send_chat(user, page, "use moldina")

    assert element(user, "param_docking_algorithm").value == "moldina"
    assert element(user, "param_max_workers").value == min(MEASURED_BEST_WORKERS["moldina"], 10)
    assert any("Docking algorithm set to **Moldina (PSO)**" in t for t in page.chat_texts())


# ---------------------------------------------------------------------------
# The LLM parse runs off the event loop (Stage 16.4)
# ---------------------------------------------------------------------------

async def test_chat_input_is_disabled_while_the_llm_parses(user: User, page, monkeypatch):
    release = threading.Event()

    def slow_parse(client, message):
        release.wait(5)
        return {"services": ["dock"], "parameters": {"protein_id": "6lu7"}}

    monkeypatch.setattr(f"{PARSER}.parse_workflow_with_llm", slow_parse)
    await user.open("/")

    set_value(user, "chat-input", "dock 6lu7")
    click(user, "chat-send")
    await eventually(lambda: page.chat.parsing)

    assert not element(user, "chat-input").enabled
    await user.should_see("Parsing with LLM...")
    release.set()
    await eventually(lambda: not page.chat.parsing)
    assert element(user, "chat-input").enabled
    assert page.state.form["param_protein_id"] == "6lu7"


async def test_parse_in_flight_is_dropped_after_new_session(user: User, page, output_base, monkeypatch):
    # Review Stage R1, deferred #2: a session change during the LLM call
    # must not receive that call's result.
    release = threading.Event()

    def slow_parse(client, message, **_kw):
        release.wait(5)
        return {"services_add": [], "parameters": {"protein_id": "1abc"}}

    monkeypatch.setattr(f"{PARSER}.parse_followup_with_llm", slow_parse)

    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.parser.validate_workflow({"services": ["dock"], "parameters": {"protein_id": "6lu7"}})

    page.seed(seed)
    await user.open("/")

    set_value(user, "chat-input", "use protein 1abc")
    click(user, "chat-send")
    await eventually(lambda: page.chat.parsing)
    click(user, "new-session")
    release.set()
    await eventually(lambda: not page.chat.parsing)

    assert page.chat_texts()[-1] == STALE_PARSE_REPLY
    assert page.state.form["param_protein_id"] == "6lu7"


async def test_llm_failure_is_the_reply(user: User, page, monkeypatch):
    def fail(client, message):
        raise RuntimeError("LLM offline")

    monkeypatch.setattr(f"{PARSER}.parse_workflow_with_llm", fail)
    await user.open("/")

    await send_chat(user, page, "dock 6lu7")

    assert page.chat_texts()[-1] == "Sorry, I couldn't parse that: LLM offline"
    assert element(user, "chat-input").enabled


# ---------------------------------------------------------------------------
# The run log (Review Stage R1, deferred #1)
# ---------------------------------------------------------------------------

async def test_run_log_streams_apart_from_the_chat(user: User, page, output_base):
    bridge = RunningBridge(lines=["STEP 1: get_input_files"])

    def seed(state):
        state.bridge = bridge
        state.job_id = "job_20260930_000000_abcdef"

    page.seed(seed)
    await user.open("/")
    await eventually(lambda: page.state.run_log == ["STEP 1: get_input_files"])

    await send_chat(user, page, "show history")
    bridge._lines = ["STEP 2: prepare_ligands"]
    await eventually(lambda: len(page.state.run_log) == 2)

    assert page.chat_texts()[-2:] == ["show history", "No active session — no history yet."]
    assert element(user, "run-log").visible


async def test_underscores_in_chat_replies_are_not_italics(user: User, page):
    # Found in Stage 16.4's real-data check: markdown2 read
    # "check164_crystal_ligand.mol2" as check164<em>crystal</em>ligand.mol2.
    page.seed(lambda state: state.messages.append(
        {"role": "assistant", "content": "Local protein file set: **check164_crystal_ligand.mol2**, prepare_ligands"}
    ))
    await user.open("/")

    html = [m.props.get("innerHTML", "") for m in user.find(kind=ui.markdown).elements]
    reply = next(h for h in html if "Local protein file set" in h)
    assert "<strong>check164_crystal_ligand.mol2</strong>" in reply
    assert "prepare_ligands" in reply and "<em>" not in reply
