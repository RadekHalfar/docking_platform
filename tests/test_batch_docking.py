"""Tests for batch docking from a local folder (Phase 0b): ligand_folder parameter.

Covers three layers:
- common/file_utils.scan_ligand_folder(): filesystem discovery of ligand files.
- WorkflowParser: get_parameter_issue() treats ligand_folder as an alternative to
  ligand_ids/ligand_files, validating it's an existing directory.
- WorkflowOrchestrator: execute_workflow() scans the folder and supersedes any
  ligand_files/ligand_ids also present in parameters.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from common.file_utils import scan_ligand_folder
from services.llm_orchestrator import WorkflowOrchestrator
from services.llm_orchestrator.workflow_parser import WorkflowParser


# ---------------------------------------------------------------------------
# scan_ligand_folder() tests
# ---------------------------------------------------------------------------

def test_scan_ligand_folder_finds_sdf_files(tmp_path):
    (tmp_path / "ATP.sdf").write_text("$$$$\n")
    (tmp_path / "HEM.sdf").write_text("$$$$\n")
    (tmp_path / "ADP.mol2").write_text("@<TRIPOS>MOLECULE\n")

    found = scan_ligand_folder(str(tmp_path))

    assert len(found) == 3
    assert all(Path(p).is_file() for p in found)


def test_scan_ligand_folder_ignores_non_ligand_files(tmp_path):
    (tmp_path / "ATP.sdf").write_text("$$$$\n")
    (tmp_path / "notes.txt").write_text("not a ligand")
    (tmp_path / "run.log").write_text("log output")

    found = scan_ligand_folder(str(tmp_path))

    assert len(found) == 1
    assert Path(found[0]).name == "ATP.sdf"


def test_scan_ligand_folder_recursive_finds_nested(tmp_path):
    (tmp_path / "ATP.sdf").write_text("$$$$\n")
    nested = tmp_path / "subdir"
    nested.mkdir()
    (nested / "HEM.sdf").write_text("$$$$\n")

    found = scan_ligand_folder(str(tmp_path))

    assert len(found) == 2
    names = {Path(p).name for p in found}
    assert names == {"ATP.sdf", "HEM.sdf"}


def test_scan_ligand_folder_empty_raises(tmp_path):
    found = scan_ligand_folder(str(tmp_path))
    assert found == []


def test_scan_ligand_folder_nonexistent_raises(tmp_path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(ValueError):
        scan_ligand_folder(str(missing))


# ---------------------------------------------------------------------------
# WorkflowParser tests
# ---------------------------------------------------------------------------

def test_ligand_folder_validates_when_set(tmp_path):
    (tmp_path / "ATP.sdf").write_text("$$$$\n")

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"ligand_folder": str(tmp_path)},
    }

    assert parser.get_parameter_issue("ligand_ids") is None


def test_nonexistent_ligand_folder_marks_invalid(tmp_path):
    missing = tmp_path / "does_not_exist"

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"ligand_folder": str(missing)},
    }

    assert parser.get_parameter_issue("ligand_ids") == "invalid"


def test_validate_workflow_no_missing_when_ligand_folder_provided(tmp_path):
    (tmp_path / "ATP.sdf").write_text("$$$$\n")

    parser = WorkflowParser()
    result = parser.validate_workflow({
        "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_folder": str(tmp_path),
            "center": [10.0, 20.0, 30.0],
            "box_size": [25.0, 30.0, 35.0],
        },
    })

    assert result["missing_required"] == []


# ---------------------------------------------------------------------------
# WorkflowOrchestrator tests
# ---------------------------------------------------------------------------

def test_execute_workflow_with_ligand_folder_uses_scan(tmp_path):
    """When ligand_folder is set, execute_workflow scans it and copies every file found."""
    ligand_dir = tmp_path / "ligands"
    ligand_dir.mkdir()
    (ligand_dir / "ATP.sdf").write_text("$$$$\n")
    (ligand_dir / "HEM.sdf").write_text("$$$$\n")

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    calls = []

    def fake_call(service_url, tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "download_structures":
            return {"protein_path": "/app/downloads/6lu7.pdb", "ligand_paths": []}
        raise KeyError(tool_name)

    orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_folder": str(ligand_dir),
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="batch_test")

    download_calls = [c for c in calls if c[0] == "download_structures"]
    assert len(download_calls) == 1
    assert download_calls[0][1]["ligand_ids"] == []

    ligand_files = results["results"]["get_input_files"]["ligand_paths"]
    assert len(ligand_files) == 2
    names = {Path(p).name for p in ligand_files}
    assert names == {"ATP.sdf", "HEM.sdf"}


def test_ligand_folder_supersedes_ligand_files_and_ids(tmp_path):
    """ligand_folder overrides ligand_files/ligand_ids entirely, not combined with them."""
    ligand_dir = tmp_path / "ligands"
    ligand_dir.mkdir()
    (ligand_dir / "ATP.sdf").write_text("$$$$\n")

    other_ligand = tmp_path / "src" / "HEM.sdf"
    other_ligand.parent.mkdir()
    other_ligand.write_text("$$$$\n")

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))

    def fake_call(service_url, tool_name, arguments):
        if tool_name == "download_structures":
            return {"protein_path": "/app/downloads/6lu7.pdb", "ligand_paths": []}
        raise KeyError(tool_name)

    orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_folder": str(ligand_dir),
            "ligand_files": [str(other_ligand)],
            "ligand_ids": ["NAD"],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="supersede_test")

    ligand_files = results["results"]["get_input_files"]["ligand_paths"]
    names = {Path(p).name for p in ligand_files}
    assert names == {"ATP.sdf"}


def test_empty_ligand_folder_raises_runtime_error(tmp_path):
    ligand_dir = tmp_path / "ligands"
    ligand_dir.mkdir()

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": "/app/downloads/6lu7.pdb", "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_folder": str(ligand_dir),
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="empty_folder_test")

    assert results["status"] == "failed"
    assert any("No ligand files found" in e for e in results["errors"])


def test_ligand_folder_same_basename_in_different_subdirs_raises(tmp_path):
    """A recursive scan can find two files sharing a basename in different
    subdirectories -- execute_workflow must raise rather than silently let
    one overwrite the other in the shared downloads dir. See finding #3."""
    ligand_dir = tmp_path / "ligands"
    sub_a = ligand_dir / "batch_a"
    sub_b = ligand_dir / "batch_b"
    sub_a.mkdir(parents=True)
    sub_b.mkdir(parents=True)
    (sub_a / "ATP.sdf").write_text("FROM_A\n")
    (sub_b / "ATP.sdf").write_text("FROM_B\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": "/app/downloads/6lu7.pdb", "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_folder": str(ligand_dir),
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="folder_collision_test")

    assert results["status"] == "failed"
    assert any("collision" in e.lower() for e in results["errors"])
    assert not (output_base / "folder_collision_test" / "downloads" / "ATP.sdf").exists()
