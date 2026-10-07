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


def test_collect_missing_parameters_uses_live_pymol_box(monkeypatch, tmp_path):
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

    parser._live_box_provider = lambda: {
        "center": [9.0, 8.0, 7.0],
        "box_size": [30.0, 31.0, 32.0],
        "job_id": "job_123",
        "path": str(tmp_path / "job_123" / "docking_box.json"),
    }

    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters(parser, str(tmp_path))

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [9.0, 8.0, 7.0]
    assert params["box_size"] == [30.0, 31.0, 32.0]
    assert "center" not in parser.get_missing_parameters()
    assert "box_size" not in parser.get_missing_parameters()


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

    answers = iter(["1", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters(parser, str(tmp_path))

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [10.0, 20.0, 30.0]
    assert params["box_size"] == [25.0, 30.0, 35.0]


def test_collect_missing_parameters_falls_back_to_manual_when_live_pymol_fails(monkeypatch, tmp_path):
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

    parser._live_box_provider = lambda: None

    answers = iter(["2", "y", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters(parser, str(tmp_path))

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

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

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
        str(tmp_path),
        llm_client=llm_client,
        max_turns=1,
    )

    assert parser.has_missing_parameters()
    assert "protein_id" in parser.get_missing_parameters()


def test_collect_missing_parameters_hybrid_uses_llm_manual_box_choice(monkeypatch, tmp_path, llm_client):
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

    monkeypatch.setattr(cli, "extract_box_source_choice_with_llm", lambda *_args, **_kwargs: "manual")

    llm_client.generate.side_effect = [
        "Please provide docking box center as x,y,z.",
        '{"parameter_updates": {"center": [11.0, 22.0, 33.0], "box_size": [24.0, 25.0, 26.0]}}',
    ]

    answers = iter(["manual please", "center 11,22,33 and box size 24,25,26"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [11.0, 22.0, 33.0]
    assert params["box_size"] == [24.0, 25.0, 26.0]


def test_collect_missing_parameters_hybrid_uses_llm_pymol_box_choice(monkeypatch, tmp_path, llm_client):
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

    parser._live_box_provider = lambda: {
        "center": [9.0, 8.0, 7.0],
        "box_size": [30.0, 31.0, 32.0],
        "path": str(tmp_path / "job_123" / "docking_box.json"),
    }

    monkeypatch.setattr(cli, "extract_box_source_choice_with_llm", lambda *_args, **_kwargs: "pymol")

    answers = iter(["please use pymol"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [9.0, 8.0, 7.0]
    assert params["box_size"] == [30.0, 31.0, 32.0]


def test_collect_missing_parameters_hybrid_box_choice_falls_back_to_menu(monkeypatch, tmp_path, llm_client):
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

    monkeypatch.setattr(cli, "extract_box_source_choice_with_llm", lambda *_args, **_kwargs: None)

    llm_client.generate.side_effect = [
        "Please provide docking box center as x,y,z.",
        '{"parameter_updates": {"center": [10.0, 20.0, 30.0], "box_size": [25.0, 30.0, 35.0]}}',
    ]

    answers = iter([
        "whatever",
        "1",
        "center 10,20,30 and box size 25,30,35",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [10.0, 20.0, 30.0]
    assert params["box_size"] == [25.0, 30.0, 35.0]


def test_collect_missing_parameters_hybrid_allows_state_during_box_source_prompt(monkeypatch, tmp_path, llm_client):
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

    monkeypatch.setattr(cli, "extract_box_source_choice_with_llm", lambda *_args, **_kwargs: "manual")

    llm_client.generate.side_effect = [
        "Please provide docking box center as x,y,z.",
        '{"parameter_updates": {"center": [11.0, 22.0, 33.0], "box_size": [24.0, 25.0, 26.0]}}',
    ]

    answers = iter(["show state", "manual please", "center 11,22,33 and box size 24,25,26"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

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
    assert "manual/default/PyMOL selection are handled elsewhere" in prompt
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

    def fake_hybrid(parser_obj, _output_base, llm_client, max_turns=8):
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

    def fake_legacy(parser_obj, _output_base):
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
    monkeypatch.setattr(cli, "_integrate_with_pymol_interactive", lambda _results: None)

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


def test_create_box_via_live_pymol_requires_rpc_ready(monkeypatch, tmp_path, capsys):
    orchestrator = Mock()
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1",
        "errors": [],
    }

    class _HealthResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok", "service": "pymol_interactive", "pymol_rpc": False}

    monkeypatch.setattr(cli.requests, "get", lambda *_args, **_kwargs: _HealthResponse())
    monkeypatch.setattr(cli, "_is_running_in_container", lambda: True)

    result = cli._create_box_via_live_pymol(
        orchestrator=orchestrator,
        workflow={"parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]}},
        job_id="job_1",
    )

    assert result is None
    assert orchestrator.execute_workflow.call_count == 0
    output = capsys.readouterr().out
    assert "pymol_interactive is not ready." in output
    assert "PyMOL GUI/RPC backend is not available yet" in output


def test_create_box_via_live_pymol_reports_load_job_error(monkeypatch, tmp_path, capsys):
    orchestrator = Mock()
    orchestrator.execute_workflow.return_value = {
        "job_id": "job_1",
        "errors": [],
    }

    class _HealthResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok", "service": "pymol_interactive", "pymol_rpc": True}

    class _LoadResponse:
        status_code = 500
        text = '{"error":"Failed to load in PyMOL session: rpc refused"}'

    monkeypatch.setattr(cli.requests, "get", lambda *_args, **_kwargs: _HealthResponse())
    monkeypatch.setattr(cli.requests, "post", lambda *_args, **_kwargs: _LoadResponse())
    monkeypatch.setattr(cli, "_is_running_in_container", lambda: True)
    monkeypatch.setattr(cli.time, "sleep", lambda *_args, **_kwargs: None)

    result = cli._create_box_via_live_pymol(
        orchestrator=orchestrator,
        workflow={"parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]}},
        job_id="job_1",
    )

    assert result is None
    output = capsys.readouterr().out
    assert "Could not auto-load prepared structures into interactive PyMOL." in output
    assert "Failed to load in PyMOL session: rpc refused" in output


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


# ---------------------------------------------------------------------------
# Phase 3: Partial rerun integration tests
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
# Stage 12.3: _handle_set_protein_geometry (CLI wiring for "set up box
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
# Stage 15.2: docking_algorithm from the CLI ("use vina", "dock with vina")
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
    """Old checkpoints without the key read as moldina (finding 9)."""
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
    """Finding 6: "dock with vina" switches the algorithm and reruns only
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
# Phase 7: Session UX tests
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
# _is_running_in_container — env-var detection (L8 fix)
# ---------------------------------------------------------------------------

def test_is_running_in_container_returns_true_when_env_set(monkeypatch):
    """CONTAINER env var alone should be sufficient to detect container context."""
    monkeypatch.setenv("CONTAINER", "1")
    assert cli._is_running_in_container() is True


def test_is_running_in_container_returns_false_without_env_or_dockerenv(monkeypatch):
    """On a bare host without /.dockerenv the function should return False."""
    import os
    monkeypatch.delenv("CONTAINER", raising=False)
    if not os.path.exists("/.dockerenv"):
        assert cli._is_running_in_container() is False


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
# Phase 10: Multi-intent command sequencing
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
    must name that bundle, not the original (Stage 13.3, Critical Finding 9)."""
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
    produce -- the CLI side of Stage 13.3's GUI guard (Review Stage R1,
    Phase 13)."""
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
# Review Stage R1, Phase 15 (docs/reviews/stage_15_r1.md)
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
    """Correctness #8: a checkpoint from before Phase 15 lacks
    docking_algorithm; restating the algorithm that ran is not a change."""
    session, _ = _algo_session(tmp_path, "job_old_session")
    monkeypatch.setattr(
        cli, "extract_updates_from_command_with_llm",
        lambda _llm, _text: {"docking_algorithm": "moldina"},
    )
    assert cli._handle_update_parameters("go back to the PSO search please", llm_client, session) == set()


def test_dock_with_vina_after_cancelled_first_workflow_runs_every_planned_step(monkeypatch, tmp_path, llm_client):
    """Found by Review Stage R1, Phase 15's real-data run (bug since
    2026-04-14): a workflow declined at "Execute this workflow?" recorded
    its planned steps as executed, so "dock with vina" planned docking
    alone and failed with "No prepared protein available for docking"."""
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
    did nothing. It shares _rerun_pending now (Review Stage R1, Phase 15)."""
    session, _ = _algo_session(tmp_path, "job_search_after_cancel")
    session.last_results = {"status": "cancelled", "services_executed": [],
                            "services_planned": list(_ALGO_SERVICES), "results": {}}
    monkeypatch.setattr(cli, "_handle_search_ligand", lambda **_kw: True)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *_a, **_kw: True)
    with patch.object(cli, "_execute_partial_rerun", return_value={"status": "completed"}) as rerun:
        _dispatch({"intent": "search_ligand", "name": "aspirin"}, session)

    assert rerun.call_args.kwargs["changed_params"] == {"ligand_ids"}
