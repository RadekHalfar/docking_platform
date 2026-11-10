"""Tests for nlp_orchestrator interactive parameter collection helpers."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from services.llm_orchestrator import WorkflowParser, InteractiveSession, WorkflowStateManager
from services.llm_orchestrator.workflow_llm_parser import (
    extract_updates_with_llm,
    generate_llm_follow_up_question,
)
import nlp_orchestrator as cli


def test_collect_missing_parameters_manual_defaults(monkeypatch, tmp_path):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
            },
        }
    )

    answers = iter(["y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters(parser)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [10.0, 20.0, 30.0]
    assert params["box_size"] == [25.0, 30.0, 35.0]


def test_collect_missing_parameters_hybrid_updates_with_llm(monkeypatch, tmp_path, llm_client):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["get_input_files"],
            "parameters": {
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client.generate.side_effect = [
        "Please provide a valid protein PDB ID (4 characters).",
        '{"parameter_updates": {"protein_id": "6lu7"}}',
    ]

    answers = iter(["Use 6lu7"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, llm_client=llm_client)

    assert parser.parsed_workflow["parameters"]["protein_id"] == "6lu7"
    assert not parser.has_missing_parameters()


def test_collect_missing_parameters_hybrid_stops_after_max_turns(monkeypatch, tmp_path, llm_client):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["get_input_files"],
            "parameters": {
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client.generate.side_effect = [
        "Please provide protein id.",
        '{"parameter_updates": {}}',
    ]

    answers = iter(["not sure"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(
        parser,
        llm_client=llm_client,
        max_turns=1,
    )

    assert parser.has_missing_parameters()
    assert "protein_id" in parser.get_missing_parameters()


def test_collect_missing_parameters_hybrid_asks_for_box_directly(monkeypatch, tmp_path, llm_client):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client.generate.side_effect = [
        "Please provide docking box center as x,y,z.",
        '{"parameter_updates": {"center": [11.0, 22.0, 33.0], "box_size": [24.0, 25.0, 26.0]}}',
    ]

    answers = iter(["center 11,22,33 and box size 24,25,26"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [11.0, 22.0, 33.0]
    assert params["box_size"] == [24.0, 25.0, 26.0]


def test_collect_missing_parameters_hybrid_allows_state_during_box_prompt(monkeypatch, tmp_path, llm_client):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client.generate.side_effect = [
        "Please provide docking box center as x,y,z.",
        "Please provide docking box center as x,y,z.",
        '{"parameter_updates": {"center": [11.0, 22.0, 33.0], "box_size": [24.0, 25.0, 26.0]}}',
    ]

    answers = iter(["show state", "center 11,22,33 and box size 24,25,26"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [11.0, 22.0, 33.0]
    assert params["box_size"] == [24.0, 25.0, 26.0]


def test_extract_updates_with_llm_prompt_includes_strict_rules(llm_client):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
            },
        }
    )

    llm_client.generate.return_value = '{"parameter_updates": {}}'

    extract_updates_with_llm(
        llm_client=llm_client,
        parser_obj=parser,
        user_answer="use ATP and two HEM ligands",
        target_param="ligand_ids",
    )

    call_args = llm_client.generate.call_args
    assert call_args is not None

    prompt = call_args.args[0]
    system = call_args.kwargs["system"]

    assert "Do not invent values" in system
    assert "Preserve multiplicity exactly" in prompt
    assert "manual/default selection are handled elsewhere" in prompt
    assert "use ATP and two HEM ligands" in prompt


def test_process_workflow_uses_hybrid_mode_by_default(monkeypatch, tmp_path, llm_client):
    parsed = {
        "services": ["dock"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP"],
        },
        "missing_required": ["center", "box_size"],
    }

    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *_args, **_kwargs: parsed)

    hybrid_called = {"value": False}

    def fake_hybrid(parser_obj, llm_client, max_turns=8):
        hybrid_called["value"] = True
        parser_obj.update_parameters({
            "center": [10.0, 20.0, 30.0],
            "box_size": [25.0, 30.0, 35.0],
        })
        parser_obj.validate_workflow(
            {
                "services": parser_obj.parsed_workflow["services"],
                "parameters": parser_obj.parsed_workflow["parameters"],
            }
        )

    monkeypatch.setattr(cli, "_collect_missing_parameters_hybrid", fake_hybrid)
    monkeypatch.setattr(
        cli,
        "_collect_missing_parameters",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy path should not be called")),
    )

    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1",
        "status": "completed",
        "services_executed": ["dock"],
        "errors": [],
    }

    parser_obj = WorkflowParser()
    result = cli.process_workflow("Dock it", llm_client, orchestrator, parser_obj)

    assert result == 0
    assert hybrid_called["value"] is True


def test_process_workflow_legacy_mode_uses_legacy_collector(monkeypatch, tmp_path, llm_client):
    parsed = {
        "services": ["dock"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP"],
        },
        "missing_required": ["center", "box_size"],
    }

    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *_args, **_kwargs: parsed)

    legacy_called = {"value": False}

    monkeypatch.setattr(
        cli,
        "_collect_missing_parameters_hybrid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("hybrid path should not be called")),
    )

    def fake_legacy(parser_obj):
        legacy_called["value"] = True
        parser_obj.update_parameters({
            "center": [10.0, 20.0, 30.0],
            "box_size": [25.0, 30.0, 35.0],
        })
        parser_obj.validate_workflow(
            {
                "services": parser_obj.parsed_workflow["services"],
                "parameters": parser_obj.parsed_workflow["parameters"],
            }
        )

    monkeypatch.setattr(cli, "_collect_missing_parameters", fake_legacy)

    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1",
        "status": "completed",
        "services_executed": ["dock"],
        "errors": [],
    }

    parser_obj = WorkflowParser()
    result = cli.process_workflow(
        "Dock it",
        llm_client,
        orchestrator,
        parser_obj,
        clarification_mode=cli.CLARIFICATION_MODE_LEGACY,
    )

    assert result == 0
    assert legacy_called["value"] is True


def test_process_workflow_auto_resolves_named_ligands(monkeypatch, tmp_path, llm_client):
    """Named ligands in normal workflow input should trigger search+selection before execution."""
    parsed = {
        "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": ["aspirin", "aspirin"],
            "center": [10.0, 10.0, 10.0],
            "box_size": [25.0, 25.0, 25.0],
        },
        "missing_required": [],
    }

    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *_args, **_kwargs: parsed)

    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.services = {"rcsb_downloader": "http://rcsb-downloader:8001"}
    orchestrator.call_mcp_tool.return_value = {
        "query": "aspirin",
        "candidates": [
            {"component_id": "AIN", "name": "AIN", "score": 0.95},
            {"component_id": "ASP", "name": "ASP", "score": 0.72},
        ],
    }
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1",
        "status": "completed",
        "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        "errors": [],
    }

    answers = iter(["1", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    parser_obj = WorkflowParser()
    result = cli.process_workflow(
        "Download 6lu7 and two ligands for aspirin, prepare and dock and show visualization with center (10,10,10) and box size (25,25,25)",
        llm_client,
        orchestrator,
        parser_obj,
    )

    assert result == 0
    orchestrator.call_mcp_tool.assert_called_once()
    workflow_arg = orchestrator.execute_workflow.call_args.args[0]
    assert workflow_arg["parameters"]["ligand_ids"] == ["AIN", "AIN"]


def test_process_workflow_cancels_when_named_ligand_not_selected(monkeypatch, tmp_path, llm_client):
    """Workflow should stop if required ligand selection is cancelled."""
    parsed = {
        "services": ["get_input_files", "dock"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": ["aspirin"],
            "center": [10.0, 10.0, 10.0],
            "box_size": [25.0, 25.0, 25.0],
        },
        "missing_required": [],
    }

    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *_args, **_kwargs: parsed)

    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.services = {"rcsb_downloader": "http://rcsb-downloader:8001"}
    orchestrator.call_mcp_tool.return_value = {
        "query": "aspirin",
        "candidates": [{"component_id": "AIN", "name": "AIN", "score": 0.95}],
    }

    # Cancel at ligand selection prompt.
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    parser_obj = WorkflowParser()
    result = cli.process_workflow(
        "Download 6lu7 and aspirin and dock with center 10,10,10 and box size 25,25,25",
        llm_client,
        orchestrator,
        parser_obj,
    )

    assert result == 1
    orchestrator.execute_workflow.assert_not_called()


def test_run_interactive_loop_keeps_active_job_and_requires_new_prefix(monkeypatch, tmp_path, llm_client):
    orchestrator = Mock()
    parser_obj = WorkflowParser()

    calls = []

    def fake_process_workflow(user_input, *_args, **kwargs):
        calls.append(user_input)
        session = kwargs.get("session")
        if session is not None:
            state_manager = WorkflowStateManager(Path(tmp_path) / "job_123")
            state_manager.save_parameters_checkpoint(
                {
                    "protein_id": "6lu7",
                    "ligand_ids": ["ATP"],
                },
                source="initial_parse",
            )
            session.activate("job_123", state_manager, last_results={"status": "completed"})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process_workflow)

    # Phase-2: stub classifier and extractor so the loop works without a real LLM
    _PASSTHROUGH_INTENTS = {
        "end": "end_session", "quit": "end_session", "exit": "end_session", "q": "end_session",
        "state": "show_state", "show state": "show_state", "status": "show_state",
        "help": "help", "h": "help", "?": "help",
    }

    def _fake_classify(_llm, text):
        t = text.strip().lower()
        if t in _PASSTHROUGH_INTENTS:
            return {"intent": _PASSTHROUGH_INTENTS[t], "name": None}
        if t.startswith("new "):
            return {"intent": "new_workflow", "name": None}
        return {"intent": "update_parameters", "name": None}

    monkeypatch.setattr(cli, "classify_session_intent", _fake_classify)
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["HEM"]},
    )

    answers = iter([
        "Download 6lu7 and ATP, prepare and dock",
        "change ligand to HEM",
        "state",
        "new Download 6lu7 and HEM, prepare and dock",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._run_interactive_loop(
        llm_client=llm_client,
        orchestrator=orchestrator,
        parser_obj=parser_obj,
        clarification_mode=cli.CLARIFICATION_MODE_HYBRID,
    )

    assert result == 0
    assert calls == [
        "Download 6lu7 and ATP, prepare and dock",
        "Download 6lu7 and HEM, prepare and dock",
    ]


def test_interactive_session_state_summary_reports_active_job(tmp_path):
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_state")
    state_manager.save_parameters_checkpoint(
        {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP", "ATP"],
            "center": [10.0, 20.0, 30.0],
            "box_size": [25.0, 30.0, 35.0],
        },
        source="initial_parse",
    )
    state_manager.save_execution_state_checkpoint(
        {
            "prepared_protein": "/app/prepared/6lu7.pdbqt",
            "prepared_ligands": ["/app/prepared/ATP.pdbqt", "/app/prepared/ATP_2.pdbqt"],
        },
        step="prepare",
    )
    session.activate("job_state", state_manager, last_results={"status": "completed"})

    summary = session.format_state_summary()

    assert "Session State" in summary
    assert "job_state" in summary
    assert "protein_id: 6lu7" in summary
    assert "ligand_ids: ['ATP', 'ATP']" in summary


def test_show_history_reports_the_parameters_revision_like_the_gui(tmp_path, capsys):
    # The CLI printed get_current_revision(), which
    # also counts state_update events, so after a run it named revision 2
    # while the parameters (what undo restores) were at revision 1. The GUI
    # already said 1.
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_hist")
    state_manager.save_parameters_checkpoint({"protein_id": "6lu7"}, source="initial_parse")
    state_manager.save_execution_state_checkpoint({"prepared_protein": "/p.pdbqt"}, step="prepare")
    session.activate("job_hist", state_manager, last_results={"status": "completed"})

    cli._handle_show_history(session)

    out = capsys.readouterr().out
    assert "Parameters are currently at revision 1." in out
    assert "source=initial_parse [params]" in out
    assert "step=prepare [state]" in out


# ---------------------------------------------------------------------------
# Partial rerun integration tests
# ---------------------------------------------------------------------------

def test_handle_update_parameters_returns_changed_keys(monkeypatch, tmp_path, llm_client):
    """_handle_update_parameters should return the set of actually-changed keys."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_rerun")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
        source="initial_parse",
    )
    session.activate("job_rerun", state_manager, last_results={"status": "completed", "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"]})

    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["HEM", "HEM"]},
    )

    changed = cli._handle_update_parameters("use two HEM ligands", llm_client, session)
    assert changed == {"ligand_ids"}

    # Parameters should be persisted to disk.
    params = state_manager.load_latest_parameters()
    assert params["ligand_ids"] == ["HEM", "HEM"]
    # Unchanged params kept.
    assert params["protein_id"] == "6lu7"


def test_handle_update_parameters_no_change_returns_empty(monkeypatch, tmp_path, llm_client):
    """If the extracted update is the same as current value, changed set is empty."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_nochange")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
        source="initial_parse",
    )
    session.activate("job_nochange", state_manager)

    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["ATP"]},
    )

    changed = cli._handle_update_parameters("change ligand to ATP", llm_client, session)
    assert changed == set()


# ---------------------------------------------------------------------------
# _handle_set_protein_geometry (CLI wiring for "set up box
# center as protein center" / "look across all protein")
# ---------------------------------------------------------------------------

def _mini_protein_pdb_text() -> str:
    fixture = Path(__file__).parent / "fixtures" / "mini_protein.pdb"
    return fixture.read_text()


def test_handle_set_protein_geometry_no_prepared_protein(tmp_path):
    """No prior prepare_protein result -> informational no-op, not a crash."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_nogeo")
    state_manager.save_parameters_checkpoint({"protein_id": "6lu7"}, source="initial_parse")
    session.activate("job_nogeo", state_manager, last_results=None)

    orchestrator = Mock(output_base=Path(tmp_path) / "orchestrator_output")
    changed = cli._handle_set_protein_geometry("center", session, orchestrator)
    assert changed == set()


def test_handle_set_protein_geometry_center(tmp_path):
    """Resolves the container-side pdb_path to this process's local mount,
    reads it, and persists the computed bounding-box midpoint as center."""
    local_root = Path(tmp_path) / "orchestrator_output"
    prepared_pdb = local_root / "job_geo" / "prepared" / "6lu7_prepared.pdb"
    prepared_pdb.parent.mkdir(parents=True, exist_ok=True)
    prepared_pdb.write_text(_mini_protein_pdb_text())

    session = InteractiveSession()
    state_manager = WorkflowStateManager(local_root / "job_geo")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "center": [1, 1, 1], "box_size": [5, 5, 5]},
        source="initial_parse",
    )
    session.activate(
        "job_geo",
        state_manager,
        last_results={
            "results": {
                "prepare_protein": {
                    "prepared_proteins": [
                        {"pdb_path": "/app/orchestrator_output/job_geo/prepared/6lu7_prepared.pdb"}
                    ]
                }
            }
        },
    )

    orchestrator = Mock(output_base=local_root)
    changed = cli._handle_set_protein_geometry("center", session, orchestrator)

    assert changed == {"center"}
    params = state_manager.load_latest_parameters()
    assert params["center"] == [6.0, 12.0, 18.0]
    assert params["box_size"] == [5, 5, 5]  # untouched


def test_handle_set_protein_geometry_box_size(tmp_path):
    local_root = Path(tmp_path) / "orchestrator_output"
    prepared_pdb = local_root / "job_geo2" / "prepared" / "6lu7_prepared.pdb"
    prepared_pdb.parent.mkdir(parents=True, exist_ok=True)
    prepared_pdb.write_text(_mini_protein_pdb_text())

    session = InteractiveSession()
    state_manager = WorkflowStateManager(local_root / "job_geo2")
    state_manager.save_parameters_checkpoint({"protein_id": "6lu7"}, source="initial_parse")
    session.activate(
        "job_geo2",
        state_manager,
        last_results={
            "results": {
                "prepare_protein": {
                    "prepared_proteins": [
                        {"pdb_path": "/app/orchestrator_output/job_geo2/prepared/6lu7_prepared.pdb"}
                    ]
                }
            }
        },
    )

    orchestrator = Mock(output_base=local_root)
    changed = cli._handle_set_protein_geometry("box_size", session, orchestrator)

    assert changed == {"box_size"}
    params = state_manager.load_latest_parameters()
    assert params["box_size"] == [11.0, 22.0, 33.0]


def test_handle_set_protein_geometry_file_missing_on_disk(tmp_path):
    """pdb_path is known but the file isn't actually there -> no-op, no crash."""
    local_root = Path(tmp_path) / "orchestrator_output"

    session = InteractiveSession()
    state_manager = WorkflowStateManager(local_root / "job_geo3")
    state_manager.save_parameters_checkpoint({"protein_id": "6lu7"}, source="initial_parse")
    session.activate(
        "job_geo3",
        state_manager,
        last_results={
            "results": {
                "prepare_protein": {
                    "prepared_proteins": [
                        {"pdb_path": "/app/orchestrator_output/job_geo3/prepared/6lu7_prepared.pdb"}
                    ]
                }
            }
        },
    )

    orchestrator = Mock(output_base=local_root)
    changed = cli._handle_set_protein_geometry("center", session, orchestrator)
    assert changed == set()


# ---------------------------------------------------------------------------
# Docking_algorithm from the CLI ("use vina", "dock with vina")
# ---------------------------------------------------------------------------

_ALGO_SERVICES = ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]


def _algo_session(tmp_path, name, **extra_params):
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / name)
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35],
         **extra_params},
        source="initial_parse",
    )
    session.activate(
        name, state_manager,
        last_results={"status": "completed", "services_executed": list(_ALGO_SERVICES), "results": {}},
    )
    return session, state_manager


def test_handle_set_docking_algorithm_persists(tmp_path):
    session, state_manager = _algo_session(tmp_path, "job_algo")
    changed = cli._handle_set_docking_algorithm("vina", session)
    assert changed == {"docking_algorithm"}
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "vina"


def test_handle_set_docking_algorithm_unchanged_is_noop(tmp_path, capsys):
    """Old checkpoints without the key read as moldina."""
    session, state_manager = _algo_session(tmp_path, "job_algo_same")
    revision = state_manager.get_current_revision()
    assert cli._handle_set_docking_algorithm("moldina", session) == set()
    assert state_manager.get_current_revision() == revision
    assert "already Moldina (PSO)" in capsys.readouterr().out


def _run_loop_after_job(monkeypatch, tmp_path, llm_client, commands, name):
    """Drive the real interactive loop: the first input starts a (faked)
    job, then *commands* run through the real intent classification."""
    orchestrator = Mock()
    orchestrator.execute_workflow.return_value = {
        "status": "completed", "services_executed": ["dock", "visualize"], "results": {}, "errors": [],
    }
    holder = {}

    def fake_process(_user_input, *_args, **kwargs):
        sess = kwargs["session"]
        _, sm = _algo_session(tmp_path, name)
        sess.activate(name, sm, last_results={
            "status": "completed", "services_executed": list(_ALGO_SERVICES), "results": {},
        })
        holder["state_manager"] = sm
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    answers = iter(["Download 6lu7 and ATP, prepare and dock", *commands, "end"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    cli._run_interactive_loop(llm_client, orchestrator, WorkflowParser(), cli.CLARIFICATION_MODE_HYBRID)
    return orchestrator, holder["state_manager"]


def test_interactive_loop_dock_with_vina_switches_and_redocks(monkeypatch, tmp_path, llm_client):
    """"dock with vina" switches the algorithm and reruns only
    dock + visualize -- not the plain full rerun the "dock" prefix gives."""
    orchestrator, state_manager = _run_loop_after_job(
        monkeypatch, tmp_path, llm_client, ["dock with vina", "y"], "job_loop_vina",
    )

    orchestrator.execute_workflow.assert_called_once()
    workflow = orchestrator.execute_workflow.call_args.args[0]
    assert workflow["services"] == ["dock", "visualize"]
    assert workflow["parameters"]["docking_algorithm"] == "vina"
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "vina"
    llm_client.generate.assert_not_called()


def test_interactive_loop_use_algorithm_only_switches(monkeypatch, tmp_path, capsys, llm_client):
    orchestrator, state_manager = _run_loop_after_job(
        monkeypatch, tmp_path, llm_client, ["use vina"], "job_loop_use",
    )

    orchestrator.execute_workflow.assert_not_called()
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "vina"
    assert "Type 'rerun' or 'dock'" in capsys.readouterr().out


def test_interactive_loop_use_vina_then_rerun_redocks_dock_only(monkeypatch, tmp_path, llm_client):
    orchestrator, _ = _run_loop_after_job(
        monkeypatch, tmp_path, llm_client, ["use vina", "rerun", "y"], "job_loop_rerun",
    )

    workflow = orchestrator.execute_workflow.call_args.args[0]
    assert workflow["services"] == ["dock", "visualize"]
    assert workflow["parameters"]["docking_algorithm"] == "vina"


def test_handle_update_parameters_normalizes_algorithm_alias(monkeypatch, tmp_path, llm_client):
    session, state_manager = _algo_session(tmp_path, "job_algo_alias")
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"docking_algorithm": "AutoDock Vina"},
    )
    changed = cli._handle_update_parameters("please dock using autodock vina next time", llm_client, session)
    assert changed == {"docking_algorithm"}
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "vina"


def test_handle_update_parameters_drops_unknown_algorithm(monkeypatch, tmp_path, capsys, llm_client):
    session, state_manager = _algo_session(tmp_path, "job_algo_bad")
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"docking_algorithm": "gnina", "ph": 6.5},
    )
    changed = cli._handle_update_parameters("use gnina at ph 6.5", llm_client, session)
    assert changed == {"ph"}
    params = state_manager.load_latest_parameters()
    assert params["docking_algorithm"] == "moldina"  # unchanged (the default)
    assert params["ph"] == 6.5
    assert "'gnina' is not recognised" in capsys.readouterr().out


def test_handle_search_ligand_updates_session_parameters(monkeypatch, tmp_path):
    """Searching ligands by name should persist selected component ID."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_search")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
        source="initial_parse",
    )
    session.activate("job_search", state_manager, last_results={"status": "completed", "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"]})

    orchestrator = Mock()
    orchestrator.services = {"rcsb_downloader": "http://rcsb-downloader:8001"}
    orchestrator.call_mcp_tool.return_value = {
        "query": "aspirin",
        "candidates": [
            {"component_id": "AIN", "name": "AIN", "score": 0.95},
            {"component_id": "ASP", "name": "ASP", "score": 0.76},
        ],
    }

    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    applied = cli._handle_search_ligand(orchestrator=orchestrator, session=session, query_name="aspirin")

    assert applied is True
    params = state_manager.load_latest_parameters()
    assert params["ligand_ids"] == ["AIN"]
    assert session.pending_changed_params == {"ligand_ids"}


def test_execute_partial_rerun_calls_orchestrator(monkeypatch, tmp_path):
    """_execute_partial_rerun should invoke orchestrator.execute_workflow with the right subset."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_partial")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["HEM"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
        source="test",
    )
    session.activate(
        "job_partial",
        state_manager,
        last_results={
            "status": "completed",
            "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
            "results": {},
        },
    )

    orchestrator = Mock()
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_partial",
        "status": "completed",
        "services_executed": ["dock", "visualize"],
        "results": {},
        "errors": [],
    }

    # Auto-confirm rerun.
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    result = cli._execute_partial_rerun(session, orchestrator, changed_params={"center"})

    assert result is not None
    assert result["status"] == "completed"

    # Orchestrator must have been called with dock + visualize only.
    call_args = orchestrator.execute_workflow.call_args
    workflow_arg = call_args.args[0]
    assert workflow_arg["services"] == ["dock", "visualize"]
    assert workflow_arg["parameters"]["ligand_ids"] == ["HEM"]


def test_execute_partial_rerun_no_active_job(monkeypatch, tmp_path):
    session = InteractiveSession()
    orchestrator = Mock()

    result = cli._execute_partial_rerun(session, orchestrator, changed_params={"center"})
    assert result is None
    orchestrator.execute_workflow.assert_not_called()


def test_interactive_loop_update_triggers_rerun(monkeypatch, tmp_path, capsys, llm_client):
    """In the interactive loop, update_parameters should automatically trigger partial rerun."""
    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_loop",
        "status": "completed",
        "services_executed": ["dock", "visualize"],
        "results": {},
        "errors": [],
    }
    parser_obj = WorkflowParser()

    # Set up a pre-existing session via fake process_workflow.
    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(Path(tmp_path) / "job_loop")
            sm.save_parameters_checkpoint(
                {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
                source="initial_parse",
            )
            sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")  # the run happened
            sess.activate("job_loop", sm, last_results={
                "status": "completed",
                "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
                "results": {},
            })
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: (
            {"intent": "end_session", "name": None}
            if text.strip().lower() in ("end", "quit", "exit", "q")
            else {"intent": "update_parameters", "name": None}
        ),
    )
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"center": [1, 2, 3]},
    )

    answers = iter([
        "Download 6lu7 and ATP dock",   # triggers fake_process
        "set center to 1,2,3",           # triggers update (no auto-rerun)
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    output = capsys.readouterr().out
    assert "Parameters updated" in output
    assert "rerun" in output.lower() or "dock" in output.lower()


# ---------------------------------------------------------------------------
# Session UX tests
# ---------------------------------------------------------------------------

def test_interactive_loop_prompt_shows_job_id_when_active(monkeypatch, tmp_path, llm_client):
    """After a job is activated the interactive prompt must include the job ID."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    prompts_seen: list = []

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(Path(tmp_path) / "job_prompt")
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="test")
            sess.activate("job_prompt", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    # No deterministic pattern should fire before the job is active.
    monkeypatch.setattr(cli, "_deterministic_intent", lambda _text: None)
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: {"intent": "end_session"} if text.strip().lower() == "end" else {"intent": "show_state"},
    )

    call_n = [0]

    def fake_input(prompt):
        prompts_seen.append(prompt)
        call_n[0] += 1
        if call_n[0] == 1:
            return "dock 6lu7 with ATP"   # triggers fake_process → activates job
        if call_n[0] == 2:
            return "state"                # one turn with active job
        return "end"

    monkeypatch.setattr("builtins.input", fake_input)
    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    # At least one prompt after job activation must contain the job ID.
    assert any("job_prompt" in p for p in prompts_seen[1:])


def test_interactive_loop_invalid_command_does_not_terminate(monkeypatch, tmp_path, capsys, llm_client):
    """An unrecognised command must print a guidance message and keep the session alive."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(Path(tmp_path) / "job_invalid")
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="test")
            sess.activate("job_invalid", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    # Classify as "unknown" for everything except the final "end".
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: (
            {"intent": "end_session"} if text.strip().lower() == "end"
            else {"intent": "unknown"}
        ),
    )

    answers = iter(["dock 6lu7 with ATP", "xyzzy nonsense command", "end"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    assert result == 0  # session ended cleanly
    output = capsys.readouterr().out
    assert "help" in output.lower()  # guidance must be printed


# ---------------------------------------------------------------------------
# `compare` subcommand
# ---------------------------------------------------------------------------

def test_run_compare_command_no_args_prints_usage(capsys):
    """Calling `compare` with no job dirs prints usage and returns exit code 1."""
    exit_code = cli._run_compare_command([])

    assert exit_code == 1
    assert "Usage" in capsys.readouterr().out


def test_run_compare_command_missing_job_dir_reports_error(tmp_path, capsys):
    """A nonexistent job directory should produce a friendly error, not a traceback."""
    exit_code = cli._run_compare_command([str(tmp_path / "no_such_job")])

    assert exit_code == 1
    assert "❌" in capsys.readouterr().out


def test_run_compare_command_prints_table(tmp_path, capsys):
    """A successful comparison prints a ligand x job ASCII table."""
    import json

    job_dir = tmp_path / "job_abc"
    job_dir.mkdir()
    (job_dir / "workflow_results.json").write_text(json.dumps({
        "results": {"dock": {"results": [{"ligand_id": "ATP", "best_affinity": -8.4}]}}
    }))

    exit_code = cli._run_compare_command([str(job_dir)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ATP" in output
    assert "-8.4" in output
    assert "job_abc" in output



# ---------------------------------------------------------------------------
# `cleanup-jobs` subcommand
# ---------------------------------------------------------------------------

def _make_job(base, days_ago: int, suffix: str = "abc123"):
    import datetime as dt
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    job_id = f"job_{when.strftime('%Y%m%d_%H%M%S')}_{suffix}"
    (base / job_id).mkdir(parents=True)
    return job_id


def test_run_cleanup_jobs_command_requires_a_filter(tmp_path, capsys):
    """Neither --keep-last nor --older-than-days given must refuse to run,
    rather than silently defaulting to 'everything is prunable'."""
    exit_code = cli._run_cleanup_jobs_command(["--output-dir", str(tmp_path)])

    assert exit_code == 1
    assert "at least one" in capsys.readouterr().out


def test_run_cleanup_jobs_command_dry_run_lists_without_deleting(tmp_path, capsys):
    job_id = _make_job(tmp_path, days_ago=100)

    exit_code = cli._run_cleanup_jobs_command(
        ["--output-dir", str(tmp_path), "--older-than-days", "30"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert job_id in output
    assert "Dry run" in output
    assert (tmp_path / job_id).exists()  # not deleted


def test_run_cleanup_jobs_command_with_yes_actually_deletes(tmp_path, capsys):
    job_id = _make_job(tmp_path, days_ago=100)

    exit_code = cli._run_cleanup_jobs_command(
        ["--output-dir", str(tmp_path), "--older-than-days", "30", "--yes"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Deleted 1" in output
    assert not (tmp_path / job_id).exists()


def test_run_cleanup_jobs_command_no_matches(tmp_path, capsys):
    _make_job(tmp_path, days_ago=1)  # too recent to match

    exit_code = cli._run_cleanup_jobs_command(
        ["--output-dir", str(tmp_path), "--older-than-days", "30"]
    )

    assert exit_code == 0
    assert "No jobs match" in capsys.readouterr().out


def test_format_comparison_table_shows_na_for_missing_values():
    """Ligands missing from a job should render as N/A rather than crashing."""
    comparison = {
        "ligands": ["ATP", "HEM"],
        "jobs": ["job_abc", "job_def"],
        "affinities": [[-8.4, None], [None, -7.1]],
    }

    table = cli._format_comparison_table(comparison)

    assert "N/A" in table
    assert "-8.4" in table
    assert "-7.1" in table


# ---------------------------------------------------------------------------
# Multi-intent command sequencing
# ---------------------------------------------------------------------------

def test_multi_intent_two_actions_run_in_order(monkeypatch, tmp_path, capsys, llm_client):
    """'state and save results as trial_a' should run both actions, in order."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain1"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint(
                {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
                source="initial_parse",
            )
            sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")  # the run happened
            (job_dir / "docking_results").mkdir(parents=True, exist_ok=True)
            (job_dir / "docking_results" / "ATP_out.pdbqt").write_text("dummy")
            sess.activate(
                "job_chain1", sm,
                last_results={
                    "status": "completed",
                    "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
                    "results": {},
                },
            )
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)

    answers = iter([
        "Download 6lu7 and ATP, prepare and dock",
        "state and save results as trial_a",
        "y",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    assert result == 0
    output = capsys.readouterr().out
    assert "Multi-step command detected (2 steps)" in output
    assert output.index("Session State") < output.index("Saved bundle 'trial_a'")
    assert "Multi-step command summary: 2/2 steps completed." in output
    assert (job_dir / "saved_results" / "trial_a" / "metadata.json").exists()


def test_multi_intent_control_then_mutation_persists_update(monkeypatch, tmp_path, capsys, llm_client):
    """'state and change ligand to HEM' should print state first, then persist the update."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain2"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint(
                {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
                source="initial_parse",
            )
            sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")  # the run happened
            sess.activate(
                "job_chain2", sm,
                last_results={
                    "status": "completed",
                    "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
                    "results": {},
                },
            )
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["HEM"]},
    )

    answers = iter([
        "Download 6lu7 and ATP, prepare and dock",
        "state and change ligand to HEM",
        "y",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    output = capsys.readouterr().out
    assert output.index("Session State") < output.index("Parameters updated")

    sm = WorkflowStateManager(job_dir)
    assert sm.load_latest_parameters()["ligand_ids"] == ["HEM"]


def test_multi_intent_second_step_failure_keeps_first_effect(monkeypatch, tmp_path, capsys, llm_client):
    """If step 2 fails, step 1's effect stays applied and the summary explains the stop."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain3"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            # Deliberately missing center/box_size so the rerun pre-flight check fails.
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
            sess.activate(
                "job_chain3", sm,
                last_results={
                    "status": "completed",
                    "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
                    "results": {},
                },
            )
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["HEM"]},
    )

    answers = iter([
        "Download 6lu7 and ATP, dock",
        "change ligand to HEM and rerun docking",
        "y",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    output = capsys.readouterr().out
    assert "required parameters are missing" in output
    assert "Multi-step command summary: 1/2 steps completed." in output
    assert 'Stopped after step 2 ("rerun docking")' in output

    sm = WorkflowStateManager(job_dir)
    assert sm.load_latest_parameters()["ligand_ids"] == ["HEM"]


def test_multi_intent_and_only_ambiguous_input_falls_back_silently(monkeypatch, tmp_path, capsys, llm_client):
    """An 'and'-only compound command that can't be split runs unchanged, with no LLM split attempt."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain4"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
            sess.activate("job_chain4", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)

    extract_calls = []

    def fake_extract(_llm, text):
        extract_calls.append(text)
        return {"center": [5.0, 6.0, 7.0], "box_size": [20.0, 20.0, 20.0]}

    monkeypatch.setattr(cli, "extract_updates_from_command_with_llm", fake_extract)

    llm_split_calls = []

    def fake_llm_split(_llm, text):
        llm_split_calls.append(text)
        return [text]

    monkeypatch.setattr(cli, "_llm_split_command_sequence", fake_llm_split)

    answers = iter([
        "Download 6lu7 and ATP, dock",
        "set center 5,6,7 and box 20,20,20",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    # Proves the old single-call path ran unchanged, and the LLM split
    # second-pass was never even attempted for an "and"-only input.
    assert extract_calls == ["set center 5,6,7 and box 20,20,20"]
    assert llm_split_calls == []
    output = capsys.readouterr().out
    assert "Note: treating this as a single command" not in output


def test_multi_intent_then_ambiguous_input_falls_back_with_guidance(monkeypatch, tmp_path, capsys, llm_client):
    """A 'then'-containing input the LLM split also can't parse prints a guidance note."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain5"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
            sess.activate("job_chain5", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(cli, "_llm_split_command_sequence", lambda _llm, text: [text])
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: {"intent": "end_session"} if text.strip().lower() == "end" else {"intent": "unknown"},
    )

    answers = iter([
        "Download 6lu7 and ATP, dock",
        "dock it then somehow fix it",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    output = capsys.readouterr().out
    assert "Note: treating this as a single command" in output


def test_multi_intent_llm_second_pass_recovers_chain(monkeypatch, tmp_path, capsys, llm_client):
    """When the deterministic splitter finds nothing, a 'then' input still gets a real LLM split attempt."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain6"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
            sess.activate("job_chain6", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(
        cli, "_llm_split_command_sequence",
        lambda _llm, _text: ["please show me the state", "then end the session"],
    )
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: {"intent": "show_state"} if "state" in text.lower() else {"intent": "end_session"},
    )

    answers = iter([
        "Download 6lu7 and ATP",
        "please show me the state then end the session",
        "y",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    assert result == 0
    output = capsys.readouterr().out
    assert "Multi-step command detected (2 steps)" in output
    assert "Session State" in output
    assert "Goodbye!" in output


def test_multi_intent_confirm_decline_cancels_chain(monkeypatch, tmp_path, capsys, llm_client):
    """Declining the multi-step confirmation must run neither step's side effect."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain7"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
            sess.activate("job_chain7", sm, last_results={"status": "completed", "services_executed": []})
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"ligand_ids": ["HEM"]},
    )

    answers = iter([
        "Download 6lu7 and ATP",
        "state and change ligand to HEM",
        "n",
        "end",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    output = capsys.readouterr().out
    assert "Multi-step command cancelled." in output

    sm = WorkflowStateManager(job_dir)
    assert sm.load_latest_parameters()["ligand_ids"] == ["ATP"]  # unchanged — neither step ran


def test_multi_intent_end_session_mid_chain_stops_session(monkeypatch, tmp_path, capsys, llm_client):
    """'save results as x and end session' should save, then end the session immediately."""
    orchestrator = Mock()
    parser_obj = WorkflowParser()
    job_dir = Path(tmp_path) / "job_chain8"

    def fake_process(user_input, *_args, **kwargs):
        sess = kwargs.get("session")
        if sess is not None:
            sm = WorkflowStateManager(job_dir)
            sm.save_parameters_checkpoint(
                {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [10, 20, 30], "box_size": [25, 30, 35]},
                source="initial_parse",
            )
            sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")  # the run happened
            (job_dir / "docking_results").mkdir(parents=True, exist_ok=True)
            (job_dir / "docking_results" / "ATP_out.pdbqt").write_text("dummy")
            sess.activate(
                "job_chain8", sm,
                last_results={
                    "status": "completed",
                    "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
                    "results": {},
                },
            )
        return 0

    monkeypatch.setattr(cli, "process_workflow", fake_process)

    answers = iter([
        "Download 6lu7 and ATP, dock",
        "save results as trial_end and end session",
        "y",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._run_interactive_loop(llm_client, orchestrator, parser_obj, cli.CLARIFICATION_MODE_HYBRID)

    assert result == 0
    output = capsys.readouterr().out
    assert "Saved bundle 'trial_end'" in output
    assert "Goodbye!" in output
    assert (job_dir / "saved_results" / "trial_end" / "metadata.json").exists()



def test_save_results_declined_overwrite_prints_final_suffixed_name(monkeypatch, tmp_path, capsys):
    """Declining the overwrite saves under name_HHMMSS -- and the success line
    must name that bundle, not the original."""
    job_dir = tmp_path / "job_save1"
    sm = WorkflowStateManager(job_dir)
    sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
    sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")
    (job_dir / "saved_results" / "trial_a").mkdir(parents=True)
    session = InteractiveSession()
    session.activate("job_save1", sm, last_results={"status": "completed", "results": {}})
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert cli._handle_save_results({"name": "trial_a", "scope": "all"}, session) is True

    output = capsys.readouterr().out
    saved = sorted(p.name for p in (job_dir / "saved_results").iterdir())
    assert saved[0] == "trial_a" and len(saved) == 2
    new_name = saved[1]
    assert new_name.startswith("trial_a_")
    assert f"Saving as '{new_name}' instead." in output
    assert f"Saved bundle '{new_name}'" in output
    assert "Saved bundle 'trial_a'" not in output


def test_save_results_refused_when_latest_parameters_never_ran(tmp_path, capsys):
    """A restored revision that never ran (e.g. in another process, so this
    one's `pending` is empty) must not be saved over results it didn't
    produce -- the CLI side of the GUI's save guard."""
    job_dir = tmp_path / "job_save2"
    sm = WorkflowStateManager(job_dir)
    sm.save_parameters_checkpoint({"protein_id": "6lu7", "ligand_ids": ["ATP"]}, source="initial_parse")
    sm.save_execution_state_checkpoint({"docking_results": {}}, step="dock")
    sm.save_parameters_checkpoint({"protein_id": "1abc", "ligand_ids": ["ATP"]}, source="interactive_update")
    sm.restore_revision(1)
    session = InteractiveSession()
    session.activate("job_save2", sm, last_results={"status": "completed", "results": {}})

    assert cli._handle_save_results({"name": "trial_a", "scope": "all"}, session) is False

    assert "have not been run yet" in capsys.readouterr().out
    assert not (job_dir / "saved_results").exists()


# ---------------------------------------------------------------------------
# Docking algorithm: LLM-classified intents, old sessions, cancelled first workflows
# ---------------------------------------------------------------------------

def _dispatch(intent_result, session, orchestrator=None):
    return cli._dispatch_intent(
        intent_result, "switch the search over", Mock(), orchestrator or Mock(), WorkflowParser(),
        session, cli.CLARIFICATION_MODE_HYBRID, None, None, None,
    )


@pytest.mark.parametrize("name", [None, "gnina", "AutoDock Vina 1.1"])
def test_llm_classified_algorithm_intent_with_bad_name_persists_nothing(tmp_path, capsys, name):
    """Correctness #4: the LLM classifier may return set_docking_algorithm
    with any name; it never reaches the checkpoint unnormalised."""
    session, state_manager = _algo_session(tmp_path, "job_bad_intent")
    revision = state_manager.get_current_revision()

    assert _dispatch({"intent": "set_docking_algorithm", "name": name}, session) is True

    assert state_manager.get_current_revision() == revision
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "moldina"
    assert "is not recognised" in capsys.readouterr().out


def test_llm_classified_algorithm_intent_alias_is_normalised(tmp_path):
    session, state_manager = _algo_session(tmp_path, "job_alias_intent")
    _dispatch({"intent": "set_docking_algorithm", "name": "AutoDock Vina"}, session)
    assert state_manager.load_latest_parameters()["docking_algorithm"] == "vina"


def test_dock_with_current_algorithm_redocks_dock_and_visualize_only(tmp_path):
    """Correctness #5: "dock with vina" on a session already using vina
    changes nothing, which fell back to a full CORE_PARAMS rerun."""
    session, _ = _algo_session(tmp_path, "job_same_algo", docking_algorithm="vina")
    orchestrator = Mock()
    with patch.object(cli, "_execute_partial_rerun", return_value={"status": "completed"}) as rerun:
        _dispatch({"intent": "set_docking_algorithm", "name": "vina", "rerun": True}, session, orchestrator)

    assert rerun.call_args.kwargs["changed_params"] == {"docking_algorithm"}


def test_llm_update_restating_default_algorithm_on_old_session_is_no_change(monkeypatch, tmp_path, llm_client):
    """A checkpoint written by an older version lacks
    docking_algorithm; restating the algorithm that ran is not a change."""
    session, _ = _algo_session(tmp_path, "job_old_session")
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"docking_algorithm": "moldina"},
    )
    assert cli._handle_update_parameters("go back to the PSO search please", llm_client, session) == set()


def test_dock_with_vina_after_cancelled_first_workflow_runs_every_planned_step(monkeypatch, tmp_path, llm_client):
    """A workflow declined at "Execute this workflow?" must not record its
    planned steps as executed, or "dock with vina" plans docking alone and
    fails with "No prepared protein available for docking"."""
    services = ["get_input_files", "prepare_ligands", "prepare_protein", "dock"]
    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *_a, **_k: {
        "services": list(services),
        "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"],
                       "center": [10.0, 10.0, 10.0], "box_size": [25.0, 25.0, 25.0]},
        "missing_required": [],
    })
    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.execute_workflow.return_value = {
        "status": "completed", "services_executed": list(services), "results": {}, "errors": [],
    }
    answers = iter(["Download 6lu7 and ATP, prepare and dock", "n", "dock with vina", "y", "end"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._run_interactive_loop(llm_client, orchestrator, WorkflowParser(), cli.CLARIFICATION_MODE_HYBRID)

    orchestrator.execute_workflow.assert_called_once()
    workflow = orchestrator.execute_workflow.call_args.args[0]
    assert workflow["services"] == services
    assert workflow["parameters"]["docking_algorithm"] == "vina"


def test_search_ligand_rerun_after_cancelled_first_workflow_still_runs(monkeypatch, tmp_path):
    """The search-ligand "rerun now?" branch checked services_executed
    itself, which a cancelled first workflow leaves empty, so "y" silently
    did nothing. It shares _rerun_pending now."""
    session, _ = _algo_session(tmp_path, "job_search_after_cancel")
    session.last_results = {"status": "cancelled", "services_executed": [],
                            "services_planned": list(_ALGO_SERVICES), "results": {}}
    monkeypatch.setattr(cli, "_handle_search_ligand", lambda **_kw: True)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *_a, **_kw: True)
    with patch.object(cli, "_execute_partial_rerun", return_value={"status": "completed"}) as rerun:
        _dispatch({"intent": "search_ligand", "name": "aspirin"}, session)

    assert rerun.call_args.kwargs["changed_params"] == {"ligand_ids"}


# ---------------------------------------------------------------------------
# No LLM configured (provider "none", the default)
# ---------------------------------------------------------------------------

@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "create_llm_client", Mock(side_effect=AssertionError("no client for 'none'")))


def _main(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr("sys.argv", ["nlp_orchestrator.py", "--output-dir", str(tmp_path), *argv])
    return cli.main()


@pytest.mark.parametrize("argv", [["dock ATP into 6lu7"], ["--interactive"], ["--list-models"]])
def test_no_llm_natural_language_prints_the_setup_message(monkeypatch, tmp_path, capsys, no_llm, argv):
    assert _main(monkeypatch, tmp_path, *argv) == 1
    out = capsys.readouterr().out
    assert out.strip() == cli.NO_LLM_MESSAGE
    assert "--profile local-llm" in out


def test_no_llm_check_health_is_healthy_when_services_are(monkeypatch, tmp_path, capsys, no_llm):
    monkeypatch.setattr(cli.WorkflowOrchestrator, "check_services_health", lambda self: {"rcsb_downloader": True})

    assert _main(monkeypatch, tmp_path, "--check-health") == 0
    assert "not configured" in capsys.readouterr().out


def test_no_llm_check_health_still_fails_on_a_down_service(monkeypatch, tmp_path, no_llm):
    monkeypatch.setattr(cli.WorkflowOrchestrator, "check_services_health", lambda self: {"rcsb_downloader": False})

    assert _main(monkeypatch, tmp_path, "--check-health") == 1


def test_a_configured_llm_is_still_health_checked(monkeypatch, tmp_path, capsys):
    client = Mock(check_health=Mock(return_value=False), endpoint="https://api.openai.com/v1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(cli, "create_llm_client", Mock(return_value=client))
    monkeypatch.setattr(cli.WorkflowOrchestrator, "check_services_health", lambda self: {"rcsb_downloader": True})

    assert _main(monkeypatch, tmp_path, "--check-health") == 1
    assert "❌ openai" in capsys.readouterr().out


def test_check_health_prints_the_url_the_client_uses(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr("services.llm_orchestrator.llm_adapters.OpenAIAdapter.check_health", lambda self: True)
    monkeypatch.setattr(cli.WorkflowOrchestrator, "check_services_health", lambda self: {"rcsb_downloader": True})

    assert _main(monkeypatch, tmp_path, "--check-health") == 0
    out = capsys.readouterr().out
    assert "✅ openai (https://api.openai.com/v1)" in out
    assert "11434" not in out


@pytest.mark.parametrize("argv", [["dock ATP into 6lu7"], ["--interactive"]])
def test_a_provider_without_a_model_says_so(monkeypatch, tmp_path, capsys, argv):
    # LLM_MODEL is empty by default; without this check the request would
    # go out with model "" and fail at the provider.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(cli, "create_llm_client", Mock(return_value=Mock()))
    monkeypatch.setattr(cli, "parse_workflow_with_llm", Mock(side_effect=AssertionError("no LLM call")))

    assert _main(monkeypatch, tmp_path, *argv) == 1
    assert capsys.readouterr().out.strip() == cli.NO_MODEL_MESSAGE.format(provider="openai")


def test_list_models_needs_no_model(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(cli, "create_llm_client", Mock(return_value=Mock(list_models=Mock(return_value=["m"]))))

    assert _main(monkeypatch, tmp_path, "--list-models") == 0
    assert "  - m" in capsys.readouterr().out


@pytest.mark.parametrize("provider, configured", [("none", False), ("", False), (None, False), (" None ", False), ("openai", True)])
def test_is_llm_configured(provider, configured):
    assert cli.is_llm_configured(provider) is configured


# ---------------------------------------------------------------------------
# Scripted input: an exhausted script fails the test instead of hanging it.
# (StopIteration is an Exception, which the interactive loop catches and
# re-prompts on; a BaseException it doesn't catch.)
# ---------------------------------------------------------------------------

class _OutOfAnswers(BaseException):
    pass


def _script(monkeypatch, *answers):
    """Feed *answers* to input(); exceptions in the list are raised. Returns the prompts seen."""
    pending = list(answers)
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        if not pending:
            raise _OutOfAnswers(f"input() called again after {len(prompts) - 1} answers: {prompt!r}")
        answer = pending.pop(0)
        if isinstance(answer, BaseException) or (isinstance(answer, type) and issubclass(answer, BaseException)):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------

def test_undo_without_a_job_is_refused(capsys):
    assert cli._handle_undo_revision("1", InteractiveSession()) is False
    assert "No active job — cannot undo." in capsys.readouterr().out


def test_undo_as_a_command_without_a_job_asks_for_a_workflow_first(capsys):
    assert _dispatch({"intent": "undo_revision", "name": "1"}, InteractiveSession()) is False
    assert "No active job yet. Start a workflow first." in capsys.readouterr().out


def test_undo_restores_the_parameters_of_an_earlier_revision(tmp_path, capsys):
    session, sm = _algo_session(tmp_path, "job_undo")
    sm.save_parameters_checkpoint(
        {"protein_id": "1abc", "ligand_ids": ["HEM"], "center": [1, 2, 3], "box_size": [20, 20, 20]},
        source="update",
    )
    session.pending_changed_params = set()

    assert _dispatch({"intent": "undo_revision", "name": "1"}, session) is True

    out = capsys.readouterr().out
    assert "✅ Revision 1 restored." in out
    assert "protein_id: 6lu7" in out
    assert "ligand_ids: ['ATP']" in out
    assert "No state checkpoint at that revision — runtime state unchanged." in out
    assert "Type 'rerun' to re-execute affected steps." in out
    assert session.pending_changed_params == set(cli.CORE_PARAMS)
    restored = sm.load_latest_parameters()
    assert (restored["protein_id"], restored["ligand_ids"]) == ("6lu7", ["ATP"])


def test_undo_of_a_state_only_revision_leaves_the_parameters_alone(tmp_path, capsys):
    session, sm = _algo_session(tmp_path, "job_undo_state")
    revision = sm.save_execution_state_checkpoint(
        {"prepared_protein": "/app/6lu7.pdbqt", "ligand_files": ["/app/ATP.sdf"]}, step="prepare_protein"
    )
    session.pending_changed_params = set()

    assert cli._handle_undo_revision(str(revision), session) is True

    out = capsys.readouterr().out
    assert "No parameter checkpoint at that revision — parameters unchanged." in out
    assert "Runtime state restored (ligand_files, prepared_protein)." in out
    assert "rerun" not in out
    assert session.pending_changed_params == set()


def test_undo_without_a_number_asks_for_one(monkeypatch, tmp_path, capsys):
    session, sm = _algo_session(tmp_path, "job_undo_ask")
    prompts = _script(monkeypatch, "1")

    assert cli._handle_undo_revision(None, session) is True

    assert prompts == ["Revision number to restore: "]
    assert "✅ Revision 1 restored." in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["two", "", EOFError, KeyboardInterrupt])
def test_undo_with_no_usable_number_is_cancelled(monkeypatch, tmp_path, capsys, answer):
    session, sm = _algo_session(tmp_path, "job_undo_bad")
    revision = sm.get_current_revision()
    _script(monkeypatch, answer)

    assert cli._handle_undo_revision("abc", session) is False

    assert "Invalid or no revision number provided. Undo cancelled." in capsys.readouterr().out
    assert sm.get_current_revision() == revision


def test_undo_of_an_unknown_revision_names_the_available_ones(tmp_path, capsys):
    session, sm = _algo_session(tmp_path, "job_undo_unknown")
    revision = sm.get_current_revision()

    assert cli._handle_undo_revision("99", session) is False

    out = capsys.readouterr().out
    assert "❌ No checkpoint files found for revision 99. Available revisions: [1]" in out
    assert sm.get_current_revision() == revision


# ---------------------------------------------------------------------------
# Legacy (deterministic) clarification prompts
# ---------------------------------------------------------------------------

def _parser_for(services, **params):
    parser = WorkflowParser()
    parser.validate_workflow({"services": services, "parameters": params})
    return parser


def test_legacy_prompt_for_an_invalid_protein_id_repeats_until_valid(monkeypatch, capsys):
    parser = _parser_for(["get_input_files"], protein_id="toolong", ligand_ids=["ATP"])
    prompts = _script(monkeypatch, "abcde", "6lu7")

    cli._collect_missing_parameters(parser)

    assert prompts[0].startswith("The protein PDB ID looks invalid.")
    assert len(prompts) == 2
    assert "That protein ID looks invalid." in capsys.readouterr().out
    assert parser.parsed_workflow["parameters"]["protein_id"] == "6lu7"
    assert not parser.has_missing_parameters()


def test_legacy_prompt_for_a_missing_protein_id(monkeypatch):
    parser = _parser_for(["get_input_files"], ligand_ids=["ATP"])
    prompts = _script(monkeypatch, "6lu7")

    cli._collect_missing_parameters(parser)

    assert prompts == ["I am missing the protein PDB ID. Please enter it (e.g., 6lu7): "]
    assert parser.parsed_workflow["parameters"]["protein_id"] == "6lu7"


def test_legacy_prompt_for_ligands_rejects_an_empty_answer(monkeypatch, capsys):
    parser = _parser_for(["dock"], protein_id="6lu7", center=[1, 2, 3], box_size=[20, 20, 20])
    prompts = _script(monkeypatch, "", " , ", "ATP, HEM")

    cli._collect_missing_parameters(parser)

    assert len(prompts) == 3
    assert capsys.readouterr().out.count("I need at least one ligand ID.") == 2
    assert parser.parsed_workflow["parameters"]["ligand_ids"] == ["ATP", "HEM"]


def test_legacy_box_prompts_take_typed_coordinates(monkeypatch, capsys):
    parser = _parser_for(["dock"], protein_id="6lu7", ligand_ids=["ATP"])
    _script(
        monkeypatch,
        "maybe", "n",                    # center: re-asked, then declines the default
        "1,2", "a,b,c", "[1, 2, 3]",     # two bad triplets, then a good one
        "no",                            # box size: declines the default
        "(30, 30, 30)",
    )

    cli._collect_missing_parameters(parser)

    out = capsys.readouterr().out
    assert "Please answer with 'y' or 'n'." in out
    assert out.count("Center format is invalid.") == 2
    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [1.0, 2.0, 3.0]
    assert params["box_size"] == [30.0, 30.0, 30.0]
    assert not parser.has_missing_parameters()


def test_legacy_box_size_prompt_rejects_a_bad_triplet(monkeypatch, capsys):
    parser = _parser_for(["dock"], protein_id="6lu7", ligand_ids=["ATP"], center=[1, 2, 3])
    _script(monkeypatch, "n", "25,25", "25,25,25")

    cli._collect_missing_parameters(parser)

    assert capsys.readouterr().out.count("Box size format is invalid.") == 1
    assert parser.parsed_workflow["parameters"]["box_size"] == [25.0, 25.0, 25.0]


def test_legacy_prompt_for_an_unknown_docking_algorithm(monkeypatch, capsys):
    parser = _parser_for(
        ["dock"], protein_id="6lu7", ligand_ids=["ATP"], center=[1, 2, 3], box_size=[20, 20, 20],
        docking_algorithm="gnina",
    )
    prompts = _script(monkeypatch, "gnina", "AutoDock Vina")

    cli._collect_missing_parameters(parser)

    assert len(prompts) == 2 and "'moldina' (PSO) or 'vina'" in prompts[0]
    assert "Please answer 'moldina' or 'vina'." in capsys.readouterr().out
    assert parser.parsed_workflow["parameters"]["docking_algorithm"] == "vina"


def test_hybrid_prompt_says_so_when_nothing_could_be_extracted(monkeypatch, capsys, llm_client):
    parser = _parser_for(["get_input_files"], ligand_ids=["ATP"])
    llm_client.generate.side_effect = [
        "Which protein should I use?",
        '{"parameter_updates": {}}',
        "Which protein should I use?",
        '{"parameter_updates": {"protein_id": "6lu7"}}',
    ]
    _script(monkeypatch, "not sure", "6lu7")

    cli._collect_missing_parameters_hybrid(parser, llm_client=llm_client)

    assert capsys.readouterr().out.count("I could not extract a valid value from that answer.") == 1
    assert parser.parsed_workflow["parameters"]["protein_id"] == "6lu7"


# ---------------------------------------------------------------------------
# process_workflow: local files and failure exits
# ---------------------------------------------------------------------------

_BOX = {"center": [10.0, 20.0, 30.0], "box_size": [25.0, 25.0, 25.0]}


def _orchestrator(tmp_path, **result):
    orchestrator = Mock()
    orchestrator.output_base = Path(tmp_path)
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1", "status": "completed", "services_executed": ["dock"], "errors": [], **result,
    }
    return orchestrator


def _local_inputs(tmp_path):
    protein = tmp_path / "receptor.pdb"
    protein.write_text("ATOM\n")
    ligands = [tmp_path / "a.sdf", tmp_path / "b.sdf"]
    for lig in ligands:
        lig.write_text("x\n$$$$\n")
    folder = tmp_path / "ligand_dir"
    folder.mkdir()
    (folder / "c.sdf").write_text("x\n$$$$\n")
    return str(protein), [str(l) for l in ligands], str(folder)


def test_process_workflow_with_local_files_adds_the_input_step(monkeypatch, tmp_path, capsys, llm_client):
    protein, ligands, _ = _local_inputs(tmp_path)
    parsed = {"services": ["prepare_ligands", "prepare_protein", "dock"], "parameters": dict(_BOX)}
    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *a, **k: parsed)
    _script(monkeypatch, "")  # Execute this workflow? [Y/n]
    orchestrator = _orchestrator(tmp_path)

    assert cli.process_workflow(
        "dock these", llm_client, orchestrator, WorkflowParser(), protein_file=protein, ligand_files=ligands
    ) == 0

    out = capsys.readouterr().out
    assert f"📁 Using local protein file: {protein}" in out
    assert f"📁 Using local ligand file(s): {', '.join(ligands)}" in out
    workflow = orchestrator.execute_workflow.call_args.args[0]
    assert "get_input_files" in workflow["services"]
    assert workflow["parameters"]["protein_file"] == protein
    assert workflow["parameters"]["ligand_files"] == ligands


def test_process_workflow_ligand_folder_supersedes_ligand_files(monkeypatch, tmp_path, capsys, llm_client):
    protein, ligands, folder = _local_inputs(tmp_path)
    parsed = {"services": ["get_input_files", "dock"], "parameters": dict(_BOX)}
    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *a, **k: parsed)
    _script(monkeypatch, "y")
    orchestrator = _orchestrator(tmp_path)

    assert cli.process_workflow(
        "dock these", llm_client, orchestrator, WorkflowParser(),
        protein_file=protein, ligand_files=ligands, ligand_folder=folder,
    ) == 0

    out = capsys.readouterr().out
    assert f"📁 Using local ligand folder: {folder}" in out
    assert "Using local ligand file(s)" not in out
    workflow = orchestrator.execute_workflow.call_args.args[0]
    assert workflow["services"].count("get_input_files") == 1
    assert workflow["parameters"]["ligand_folder"] == folder
    assert workflow["parameters"]["ligand_files"] is None  # the default; the CLI files were not added


def test_process_workflow_with_a_missing_local_file_exits_1(monkeypatch, tmp_path, capsys, llm_client):
    missing = str(tmp_path / "nope.pdb")
    parsed = {"services": ["get_input_files", "dock"], "parameters": {"ligand_ids": ["ATP"], **_BOX}}
    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *a, **k: parsed)
    monkeypatch.setattr(cli, "_collect_missing_parameters_hybrid", lambda *a, **k: None)
    orchestrator = _orchestrator(tmp_path)

    assert cli.process_workflow(
        "dock ATP", llm_client, orchestrator, WorkflowParser(), protein_file=missing
    ) == 1

    out = capsys.readouterr().out
    assert "❌ Could not resolve all required parameters." in out
    assert f"The local protein file '{missing}' is invalid or not found" in out
    orchestrator.execute_workflow.assert_not_called()


def test_process_workflow_exits_1_when_the_job_reports_errors(monkeypatch, tmp_path, capsys, llm_client):
    parsed = {"services": ["dock"], "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], **_BOX}}
    monkeypatch.setattr(cli, "parse_workflow_with_llm", lambda *a, **k: parsed)
    _script(monkeypatch, "")
    orchestrator = _orchestrator(tmp_path, status="failed", errors=["No prepared protein available for docking"])
    session = InteractiveSession()

    assert cli.process_workflow("dock", llm_client, orchestrator, WorkflowParser(), session=session) == 1

    out = capsys.readouterr().out
    assert "Status: failed" in out
    assert "❌ No prepared protein available for docking" in out
    assert "Workflow completed successfully" not in out
    assert not session.has_active_job()


def test_process_workflow_exits_1_on_an_unexpected_error(monkeypatch, tmp_path, capsys, llm_client):
    parser_obj = WorkflowParser()
    monkeypatch.setattr(
        cli, "parse_workflow_with_llm", Mock(side_effect=RuntimeError("Failed to generate response: 503"))
    )

    assert cli.process_workflow("dock", llm_client, _orchestrator(tmp_path), parser_obj) == 1

    assert "❌ Failed to process workflow: Failed to generate response: 503" in capsys.readouterr().out
    assert parser_obj.state_manager is None  # released in the finally


# ---------------------------------------------------------------------------
# main(): sub-commands, modes and flags
# ---------------------------------------------------------------------------

@pytest.fixture
def an_llm(monkeypatch):
    client = Mock(list_models=Mock(return_value=["m-1"]))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(cli, "create_llm_client", Mock(return_value=client))
    return client


@pytest.mark.parametrize("sub, handler", [("compare", "_run_compare_command"), ("cleanup-jobs", "_run_cleanup_jobs_command")])
def test_main_dispatches_the_sub_commands(monkeypatch, sub, handler):
    seen = []
    monkeypatch.setattr(cli, handler, lambda argv: seen.append(argv) or 7)
    monkeypatch.setattr("sys.argv", ["nlp_orchestrator.py", sub, "job_a", "--flag"])

    assert cli.main() == 7
    assert seen == [["job_a", "--flag"]]


def test_main_interactive_forwards_the_local_files(monkeypatch, tmp_path, an_llm):
    seen = {}
    monkeypatch.setattr(cli, "_run_interactive_loop", lambda **kw: seen.update(kw) or 0)

    assert _main(
        monkeypatch, tmp_path, "--interactive", "--protein-file", "r.pdb",
        "--ligand-files", "a.sdf", "--ligand-files", "b.sdf", "--ligand-folder", "ligs",
        "--clarification-mode", "legacy",
    ) == 0

    assert seen["llm_client"] is an_llm
    assert (seen["protein_file"], seen["ligand_files"], seen["ligand_folder"]) == ("r.pdb", ["a.sdf", "b.sdf"], "ligs")
    assert seen["clarification_mode"] == "legacy"


def test_main_runs_a_single_command_and_returns_its_code(monkeypatch, tmp_path, an_llm):
    seen = {}

    def fake_process(command, llm_client, orchestrator, parser_obj, **kwargs):
        seen.update(command=command, llm_client=llm_client, output_base=orchestrator.output_base, **kwargs)
        return 3

    monkeypatch.setattr(cli, "process_workflow", fake_process)

    assert _main(monkeypatch, tmp_path, "dock ATP into 6lu7", "--protein-file", "r.pdb") == 3
    assert seen["command"] == "dock ATP into 6lu7"
    assert seen["llm_client"] is an_llm
    assert Path(seen["output_base"]) == tmp_path
    assert seen["protein_file"] == "r.pdb"
    assert seen["clarification_mode"] == "hybrid"


def test_main_without_a_command_prints_the_help_and_exits_1(monkeypatch, tmp_path, capsys, no_llm):
    assert _main(monkeypatch, tmp_path) == 1
    assert "usage:" in capsys.readouterr().out


def test_main_list_models_with_none_found(monkeypatch, tmp_path, capsys, an_llm):
    an_llm.list_models.return_value = []

    assert _main(monkeypatch, tmp_path, "--list-models") == 0

    out = capsys.readouterr().out
    assert "Available models for provider 'openai':" in out
    assert "No models found or provider endpoint not accessible" in out


@pytest.mark.parametrize(
    "flag, verbosity",
    [("normal", cli.VERBOSITY_NORMAL), ("mcp_demo", cli.VERBOSITY_MCP_DEMO), ("low", cli.VERBOSITY_LOW)],
)
def test_main_verbose_sets_the_verbosity_the_orchestrator_gets(monkeypatch, tmp_path, an_llm, flag, verbosity):
    monkeypatch.setattr(cli, "current_verbosity", cli.current_verbosity)  # restored after the test
    seen = {}
    monkeypatch.setattr(
        cli, "process_workflow",
        lambda _c, _l, orchestrator, *_a, **_k: seen.update(v=orchestrator.verbosity) or 0,
    )

    assert _main(monkeypatch, tmp_path, "dock", "--verbose", flag) == 0
    assert cli.current_verbosity == verbosity
    assert seen["v"] == verbosity


def test_main_debug_turns_on_debug_logging(monkeypatch, tmp_path, an_llm):
    import logging
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", root.level)  # restored after the test
    monkeypatch.setattr(cli, "process_workflow", lambda *a, **k: 0)

    assert _main(monkeypatch, tmp_path, "dock", "--debug") == 0
    assert root.level == logging.DEBUG


# ---------------------------------------------------------------------------
# The interactive loop: leaving it, and surviving a failing command
# ---------------------------------------------------------------------------

def _loop(llm_client):
    return cli._run_interactive_loop(llm_client, Mock(), WorkflowParser(), cli.CLARIFICATION_MODE_HYBRID)


@pytest.mark.parametrize("ending", [KeyboardInterrupt, EOFError])
def test_ctrl_c_or_end_of_input_leaves_the_loop(monkeypatch, capsys, llm_client, ending):
    process = Mock(return_value=0)
    monkeypatch.setattr(cli, "process_workflow", process)
    prompts = _script(monkeypatch, ending)

    assert _loop(llm_client) == 0

    assert len(prompts) == 1  # not asked again
    assert "Goodbye!" in capsys.readouterr().out
    process.assert_not_called()


def test_piped_input_that_ends_without_end_exits(monkeypatch, capsys, llm_client):
    """`printf '<request>\\n' | nlp_orchestrator.py -i`: the loop runs
    the command, then stops at end of input instead of re-prompting forever."""
    process = Mock(return_value=0)
    monkeypatch.setattr(cli, "process_workflow", process)
    prompts = _script(monkeypatch, "Download 6lu7 and ATP, prepare and dock", EOFError)

    assert _loop(llm_client) == 0

    assert len(prompts) == 2
    process.assert_called_once()
    assert "Goodbye!" in capsys.readouterr().out


def test_a_failing_command_is_reported_and_the_loop_goes_on(monkeypatch, capsys, llm_client):
    process = Mock(side_effect=[RuntimeError("orchestrator exploded"), 0])
    monkeypatch.setattr(cli, "process_workflow", process)
    _script(monkeypatch, "", "   ", "Download 6lu7 and ATP, prepare and dock", "Download 6lu7 and HEM, prepare and dock", "end")

    assert _loop(llm_client) == 0

    assert "❌ Error: orchestrator exploded" in capsys.readouterr().out
    assert [c.args[0] for c in process.call_args_list] == [
        "Download 6lu7 and ATP, prepare and dock", "Download 6lu7 and HEM, prepare and dock",
    ]
