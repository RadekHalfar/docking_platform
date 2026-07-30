"""Integration tests for WorkflowOrchestrator.execute_workflow (C1).

Each test stubs call_mcp_tool directly to control service responses without
going through HTTP. This exercises the orchestration logic — step order,
state threading, error collection, result persistence — without network deps.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.llm_orchestrator import WorkflowOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download_result():
    return {"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}


def _prepare_ligands_result():
    return {"prepared_ligands": [{"pdbqt_path": "/app/prepared/ATP.pdbqt"}]}


def _prepare_protein_result():
    # orchestrator reads prepared_proteins[0].pdbqt_path → workflow_state["prepared_protein"]
    return {"prepared_proteins": [{"pdbqt_path": "/app/prepared/6lu7.pdbqt"}], "status": "ok"}


def _dock_result():
    return {
        "results": [{"output_path": "/app/docking/ATP_out.pdbqt", "best_affinity": -8.5}],
        "successful": 1,
    }


def _visualize_result():
    return {"script_path": "/app/viz/scene.pse"}


# Tool name map based on actual call_mcp_tool calls in orchestrator.py:
#   get_input_files  → "download_structures"
#   prepare_ligands  → "prepare_ligands"
#   prepare_protein  → "prepare_proteins"
#   dock             → "run_docking"
#   visualize        → "visualize_docking"
_TOOL_RESULTS = {
    "download_structures": _download_result,
    "prepare_ligands": _prepare_ligands_result,
    "prepare_proteins": _prepare_protein_result,
    "run_docking": _dock_result,
    "visualize_docking": _visualize_result,
}


def _full_chain_fake_call(service_url, tool_name, arguments, **kwargs):
    """Return synthetic MCP results keyed on tool_name.

    Accepts **kwargs (e.g. `timeout`) since call_mcp_tool's real signature
    takes an optional timeout override that some steps (like dock) pass.
    """
    factory = _TOOL_RESULTS.get(tool_name)
    if factory is None:
        raise KeyError(f"No fake result registered for tool '{tool_name}'")
    return factory()


_FULL_CHAIN_WORKFLOW = {
    "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
    "parameters": {
        "protein_id": "6lu7",
        "ligand_ids": ["ATP"],
        "center": [10.0, 20.0, 30.0],
        "box_size": [25.0, 25.0, 25.0],
        "formats": {"protein": "pdb", "ligand": "sdf"},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_execute_workflow_full_chain_calls_services_in_order(tmp_path):
    """All five steps should be called in the correct dependency order."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    call_log: list[str] = []

    def recording_call(service_url, tool_name, arguments, **kwargs):
        call_log.append(tool_name)
        return _full_chain_fake_call(service_url, tool_name, arguments, **kwargs)

    orchestrator.call_mcp_tool = Mock(side_effect=recording_call)

    results = orchestrator.execute_workflow(_FULL_CHAIN_WORKFLOW, job_id="chain_test")

    assert results["status"] == "completed"
    assert call_log == [
        "download_structures",
        "prepare_ligands",
        "prepare_proteins",
        "run_docking",
        "visualize_docking",
    ]


def test_execute_workflow_skips_step_not_in_services(tmp_path):
    """Services absent from the workflow list must not be called."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    call_log: list[str] = []

    def recording_call(service_url, tool_name, arguments):
        call_log.append(tool_name)
        return {"protein_path": "/app/6lu7.pdb", "ligand_paths": []}

    orchestrator.call_mcp_tool = Mock(side_effect=recording_call)

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": [],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }

    orchestrator.execute_workflow(workflow, job_id="skip_test")

    assert call_log == ["download_structures"]
    assert "visualize_docking" not in call_log
    assert "prepare_ligands" not in call_log


def test_execute_workflow_persists_results_file(tmp_path):
    """A workflow_results.json file must be written in the job directory."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    orchestrator.call_mcp_tool = Mock(side_effect=_full_chain_fake_call)

    results = orchestrator.execute_workflow(_FULL_CHAIN_WORKFLOW, job_id="persist_test")

    results_file = tmp_path / "persist_test" / "workflow_results.json"
    assert results_file.exists(), "workflow_results.json should be written"
    saved = json.loads(results_file.read_text())
    assert saved["status"] == "completed"
    assert saved["job_id"] == "persist_test"


def test_execute_workflow_records_error_without_crashing(tmp_path):
    """A failing step should be recorded in errors without an unhandled exception."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    orchestrator.call_mcp_tool = Mock(side_effect=RuntimeError("service offline"))

    workflow = {
        "services": ["get_input_files"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": [],
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }

    results = orchestrator.execute_workflow(workflow, job_id="error_test")

    assert results["status"] == "failed"
    assert any("service offline" in e for e in results["errors"])
