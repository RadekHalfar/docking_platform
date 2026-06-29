"""Tests for nlp_orchestrator interactive parameter collection helpers."""

from pathlib import Path
from unittest.mock import Mock

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
            "services": ["download"],
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
            "services": ["download"],
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
        "services": ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"],
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
        "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"],
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
        "services": ["download", "dock"],
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
    session.activate("job_rerun", state_manager, last_results={"status": "completed", "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock"]})

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


def test_handle_search_ligand_updates_session_parameters(monkeypatch, tmp_path):
    """Searching ligands by name should persist selected component ID."""
    session = InteractiveSession()
    state_manager = WorkflowStateManager(Path(tmp_path) / "job_search")
    state_manager.save_parameters_checkpoint(
        {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
        source="initial_parse",
    )
    session.activate("job_search", state_manager, last_results={"status": "completed", "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock"]})

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
            "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"],
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
            sess.activate("job_loop", sm, last_results={
                "status": "completed",
                "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"],
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

