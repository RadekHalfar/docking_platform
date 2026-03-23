"""Tests for nlp_orchestrator interactive parameter collection helpers."""

from services.llm_orchestrator import WorkflowParser
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
