"""Tests for local file input (Phase 0a): protein_file / ligand_files parameters.

Covers two layers:
- WorkflowParser: get_parameter_issue() treats protein_file/ligand_files as
  alternatives to protein_id/ligand_ids, with their own existence/extension checks.
- WorkflowOrchestrator: execute_workflow() skips the RCSB download for whichever
  side (protein/ligand) has a local file, and copies it into the job's downloads dir.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from services.llm_orchestrator import WorkflowOrchestrator
from services.llm_orchestrator.workflow_parser import WorkflowParser


# ---------------------------------------------------------------------------
# WorkflowParser tests
# ---------------------------------------------------------------------------

def test_protein_file_validates_when_set(tmp_path):
    protein_path = tmp_path / "6lu7.pdb"
    protein_path.write_text("HEADER\n")

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"protein_file": str(protein_path)},
    }

    assert parser.get_parameter_issue("protein_id") is None


def test_invalid_protein_file_extension_raises_invalid(tmp_path):
    bad_file = tmp_path / "6lu7.xyz"
    bad_file.write_text("not a structure")

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"protein_file": str(bad_file)},
    }

    assert parser.get_parameter_issue("protein_id") == "invalid"


def test_nonexistent_protein_file_raises_invalid(tmp_path):
    missing_path = tmp_path / "does_not_exist.pdb"

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"protein_file": str(missing_path)},
    }

    assert parser.get_parameter_issue("protein_id") == "invalid"


def test_ligand_files_validate_when_set(tmp_path):
    ligand_path = tmp_path / "ATP.sdf"
    ligand_path.write_text("$$$$\n")

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"ligand_files": [str(ligand_path)]},
    }

    assert parser.get_parameter_issue("ligand_ids") is None


def test_one_invalid_ligand_file_marks_ligand_ids_invalid(tmp_path):
    good = tmp_path / "ATP.sdf"
    good.write_text("$$$$\n")
    bad = tmp_path / "HEM.txt"
    bad.write_text("not a ligand")

    parser = WorkflowParser()
    parser.parsed_workflow = {
        "services": ["get_input_files"],
        "parameters": {"ligand_files": [str(good), str(bad)]},
    }

    assert parser.get_parameter_issue("ligand_ids") == "invalid"


def test_validate_workflow_no_missing_when_local_files_provided(tmp_path):
    protein_path = tmp_path / "6lu7.pdb"
    protein_path.write_text("HEADER\n")
    ligand_path = tmp_path / "ATP.sdf"
    ligand_path.write_text("$$$$\n")

    parser = WorkflowParser()
    result = parser.validate_workflow({
        "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
        "parameters": {
            "protein_file": str(protein_path),
            "ligand_files": [str(ligand_path)],
            "center": [10.0, 20.0, 30.0],
            "box_size": [25.0, 30.0, 35.0],
        },
    })

    assert result["missing_required"] == []


# ---------------------------------------------------------------------------
# WorkflowOrchestrator tests
# ---------------------------------------------------------------------------

def _prepare_ligands_result():
    return {"prepared_ligands": [{"pdbqt_path": "/app/prepared/ATP.pdbqt"}]}


def _prepare_protein_result():
    return {"prepared_proteins": [{"pdbqt_path": "/app/prepared/6lu7.pdbqt"}]}


def test_local_protein_file_skips_rcsb_protein_id(tmp_path):
    """When protein_file is local, download_structures must be called with protein_id=None."""
    protein_src = tmp_path / "src" / "6lu7.pdb"
    protein_src.parent.mkdir()
    protein_src.write_text("HEADER\n")

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    calls = []

    def fake_call(service_url, tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "download_structures":
            return {"protein_path": None, "ligand_paths": ["/app/ATP.sdf"]}
        raise KeyError(tool_name)

    orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_file": str(protein_src),
            "ligand_ids": ["ATP"],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    orchestrator.execute_workflow(workflow, job_id="local_protein_test")

    download_calls = [c for c in calls if c[0] == "download_structures"]
    assert len(download_calls) == 1
    assert download_calls[0][1]["protein_id"] is None
    assert download_calls[0][1]["ligand_ids"] == ["ATP"]


def test_local_ligand_files_skip_rcsb_ligand_ids(tmp_path):
    """When ligand_files is local, download_structures must be called with ligand_ids=[]."""
    ligand_src = tmp_path / "src" / "ATP.sdf"
    ligand_src.parent.mkdir()
    ligand_src.write_text("$$$$\n")

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    calls = []

    def fake_call(service_url, tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "download_structures":
            return {"protein_path": "/app/6lu7.pdb", "ligand_paths": []}
        raise KeyError(tool_name)

    orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_files": [str(ligand_src)],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    orchestrator.execute_workflow(workflow, job_id="local_ligand_test")

    download_calls = [c for c in calls if c[0] == "download_structures"]
    assert len(download_calls) == 1
    assert download_calls[0][1]["ligand_ids"] == []
    assert download_calls[0][1]["protein_id"] == "6lu7"


def test_mixed_local_and_remote_ligands_are_combined(tmp_path):
    """Local ligand_files and RCSB ligand_ids can be combined: download_structures
    is called for the RCSB side only, and the resulting ligand_files list contains
    both the downloaded path and the locally-copied path."""
    ligand_src = tmp_path / "src" / "ATP.sdf"
    ligand_src.parent.mkdir()
    ligand_src.write_text("$$$$\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    calls = []

    def fake_call(service_url, tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "download_structures":
            return {"protein_path": "/app/downloads/6lu7.pdb", "ligand_paths": ["/app/downloads/NAD.sdf"]}
        raise KeyError(tool_name)

    orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_files": [str(ligand_src)],
            "ligand_ids": ["NAD"],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="mixed_ligand_test")

    download_calls = [c for c in calls if c[0] == "download_structures"]
    assert len(download_calls) == 1
    assert download_calls[0][1]["ligand_ids"] == ["NAD"]
    assert download_calls[0][1]["protein_id"] == "6lu7"

    ligand_files = results["results"]["get_input_files"]["ligand_paths"]
    assert "/app/downloads/NAD.sdf" in ligand_files
    assert any(Path(p).name == "ATP.sdf" for p in ligand_files)
    assert len(ligand_files) == 2


def test_local_protein_and_ligand_skip_mcp_download_entirely(tmp_path):
    """When both sides are local, download_structures must not be called at all."""
    protein_src = tmp_path / "src" / "6lu7.pdb"
    ligand_src = tmp_path / "src" / "ATP.sdf"
    protein_src.parent.mkdir()
    protein_src.write_text("HEADER\n")
    ligand_src.write_text("$$$$\n")

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    orchestrator.call_mcp_tool = Mock(side_effect=AssertionError("MCP should not be called"))

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_file": str(protein_src),
            "ligand_files": [str(ligand_src)],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="fully_local_test")

    orchestrator.call_mcp_tool.assert_not_called()
    assert results["status"] == "completed"


def test_local_protein_copied_to_job_dir(tmp_path):
    protein_src = tmp_path / "src" / "6lu7.pdb"
    protein_src.parent.mkdir()
    protein_src.write_text("HEADER\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": None, "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_file": str(protein_src),
            "ligand_ids": [],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    orchestrator.execute_workflow(workflow, job_id="copy_test")

    copied = output_base / "copy_test" / "downloads" / "6lu7.pdb"
    assert copied.exists()
    assert copied.read_text() == "HEADER\n"


def test_missing_local_protein_file_raises_runtime_error(tmp_path):
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "out"))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": None, "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_file": str(tmp_path / "does_not_exist.pdb"),
            "ligand_ids": [],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="missing_file_test")

    assert results["status"] == "failed"
    assert any("not found" in e for e in results["errors"])


def test_ligand_basename_collision_raises_instead_of_overwriting(tmp_path):
    """Two different source files sharing a basename must never silently
    overwrite one another in the shared downloads dir -- see finding #3."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    ligand_a = dir_a / "ATP.sdf"
    ligand_b = dir_b / "ATP.sdf"
    ligand_a.write_text("FROM_A\n")
    ligand_b.write_text("FROM_B\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": None, "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_files": [str(ligand_a), str(ligand_b)],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="collision_test")

    assert results["status"] == "failed"
    assert any("collision" in e.lower() for e in results["errors"])
    assert any(str(ligand_a) in e and str(ligand_b) in e for e in results["errors"])
    # Neither file should have been copied -- the collision check runs before any copy.
    assert not (output_base / "collision_test" / "downloads" / "ATP.sdf").exists()


def test_same_ligand_path_listed_twice_is_not_a_collision(tmp_path):
    ligand_src = tmp_path / "src" / "ATP.sdf"
    ligand_src.parent.mkdir()
    ligand_src.write_text("$$$$\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": None, "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_files": [str(ligand_src), str(ligand_src)],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="duplicate_path_test")

    assert results["status"] == "completed"
    copied = output_base / "duplicate_path_test" / "downloads" / "ATP.sdf"
    assert copied.exists()
    assert copied.read_text() == "$$$$\n"


def test_distinct_ligand_basenames_are_unaffected(tmp_path):
    ligand_a = tmp_path / "src" / "ATP.sdf"
    ligand_b = tmp_path / "src" / "HEM.sdf"
    ligand_a.parent.mkdir()
    ligand_a.write_text("ATP\n")
    ligand_b.write_text("HEM\n")

    output_base = tmp_path / "out"
    orchestrator = WorkflowOrchestrator(output_base=str(output_base))
    orchestrator.call_mcp_tool = Mock(return_value={"protein_path": None, "ligand_paths": []})

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_files": [str(ligand_a), str(ligand_b)],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    results = orchestrator.execute_workflow(workflow, job_id="distinct_names_test")

    assert results["status"] == "completed"
    downloads = output_base / "distinct_names_test" / "downloads"
    assert (downloads / "ATP.sdf").exists()
    assert (downloads / "HEM.sdf").exists()
