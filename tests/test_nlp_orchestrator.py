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


def test_collect_missing_parameters_hybrid_updates_with_llm(monkeypatch, tmp_path):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["download"],
            "parameters": {
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client = Mock()
    llm_client.generate.side_effect = [
        "Please provide a valid protein PDB ID (4 characters).",
        '{"parameter_updates": {"protein_id": "6lu7"}}',
    ]

    answers = iter(["Use 6lu7"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

    assert parser.parsed_workflow["parameters"]["protein_id"] == "6lu7"
    assert not parser.has_missing_parameters()


def test_collect_missing_parameters_hybrid_stops_after_max_turns(monkeypatch, tmp_path):
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["download"],
            "parameters": {
                "ligand_ids": ["ATP"],
            },
        }
    )

    llm_client = Mock()
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


def test_collect_missing_parameters_hybrid_uses_llm_manual_box_choice(monkeypatch, tmp_path):
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

    llm_client = Mock()
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


def test_collect_missing_parameters_hybrid_uses_llm_pymol_box_choice(monkeypatch, tmp_path):
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

    llm_client = Mock()
    cli._collect_missing_parameters_hybrid(parser, str(tmp_path), llm_client=llm_client)

    params = parser.parsed_workflow["parameters"]
    assert params["center"] == [9.0, 8.0, 7.0]
    assert params["box_size"] == [30.0, 31.0, 32.0]


def test_collect_missing_parameters_hybrid_box_choice_falls_back_to_menu(monkeypatch, tmp_path):
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

    llm_client = Mock()
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


def test_extract_updates_with_llm_prompt_includes_strict_rules():
    parser = WorkflowParser()
    parser.validate_workflow(
        {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
            },
        }
    )

    llm_client = Mock()
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


def test_process_workflow_uses_hybrid_mode_by_default(monkeypatch, tmp_path):
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

    llm_client = Mock()
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


def test_process_workflow_legacy_mode_uses_legacy_collector(monkeypatch, tmp_path):
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

    llm_client = Mock()
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


def test_run_interactive_loop_keeps_active_job_and_requires_new_prefix(monkeypatch, tmp_path):
    llm_client = Mock()
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
    monkeypatch.setattr(
        cli, "classify_session_intent",
        lambda _llm, text: {"intent": "update_parameters", "name": None},
    )
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
