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
    # orchestrator reads prepared_proteins[0].pdbqt_path/pdb_path →
    # workflow_state["prepared_protein"]/["prepared_protein_pdb"]
    return {
        "prepared_proteins": [
            {"pdbqt_path": "/app/prepared/6lu7.pdbqt", "pdb_path": "/app/prepared/6lu7_prepared.pdb"}
        ],
        "status": "ok",
    }


def _dock_result():
    return {
        "results": [
            {
                "ligand_id": "ATP",
                "output_path": "/app/docking/ATP_out.pdbqt",
                "best_affinity": -8.5,
                "modes": [{"mode": 1, "affinity": -8.5, "rmsd_lb": 0.0, "rmsd_ub": 0.0}],
            }
        ],
        "successful": 1,
    }


def _visualize_result():
    return {
        "converted": [{"ligand_id": "ATP", "sdf_path": "/app/viz/ATP_poses.sdf", "conversion_method": "meeko"}],
        "errors": [],
        "status": "ok",
    }


# Tool name map based on actual call_mcp_tool calls in orchestrator.py:
#   get_input_files  → "download_structures"
#   prepare_ligands  → "prepare_ligands"
#   prepare_protein  → "prepare_proteins"
#   dock             → "run_docking"
#   visualize        → "convert_docked_poses" (on ligand_preparer)
_TOOL_RESULTS = {
    "download_structures": _download_result,
    "prepare_ligands": _prepare_ligands_result,
    "prepare_proteins": _prepare_protein_result,
    "run_docking": _dock_result,
    "convert_docked_poses": _visualize_result,
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
        "convert_docked_poses",
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
    assert "convert_docked_poses" not in call_log
    assert "prepare_ligands" not in call_log


def test_execute_workflow_persists_results_file(tmp_path):
    """A workflow_results.json file must be written in the job directory."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    orchestrator.call_mcp_tool = Mock(side_effect=_full_chain_fake_call)

    results = orchestrator.execute_workflow(
        _FULL_CHAIN_WORKFLOW, job_id="persist_test", job_name="Overnight batch"
    )

    results_file = tmp_path / "persist_test" / "workflow_results.json"
    assert results_file.exists(), "workflow_results.json should be written"
    saved = json.loads(results_file.read_text())
    assert saved["status"] == "completed"
    assert saved["job_id"] == "persist_test"
    assert saved["job_name"] == "Overnight batch"
    assert results["job_name"] == "Overnight batch"


def test_execute_workflow_job_name_defaults_to_empty_string(tmp_path):
    """Omitting job_name must persist an empty string, not crash or omit the key."""
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    orchestrator.call_mcp_tool = Mock(side_effect=_full_chain_fake_call)

    orchestrator.execute_workflow(_FULL_CHAIN_WORKFLOW, job_id="no_name_test")

    results_file = tmp_path / "no_name_test" / "workflow_results.json"
    saved = json.loads(results_file.read_text())
    assert saved["job_name"] == ""


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

    results = orchestrator.execute_workflow(workflow, job_id="error_test", job_name="Bad run")

    assert results["status"] == "failed"
    assert any("service offline" in e for e in results["errors"])
    assert results["job_name"] == "Bad run"

    results_file = tmp_path / "error_test" / "workflow_results.json"
    saved = json.loads(results_file.read_text())
    assert saved["job_name"] == "Bad run"


# ---------------------------------------------------------------------------
# Step preconditions: a step whose input is missing fails the job with a
# message naming what is missing, and no later service is called.
# ---------------------------------------------------------------------------

def _chain_with(overrides):
    """A full-chain fake whose per-tool results can be replaced."""
    tools = {**_TOOL_RESULTS, **overrides}

    def fake(service_url, tool_name, arguments, **kwargs):
        return tools[tool_name]()

    return fake


def _run(tmp_path, services, parameters=None, overrides=None, job_id="pre"):
    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
    orchestrator.call_mcp_tool = Mock(side_effect=_chain_with(overrides or {}))
    params = dict(_FULL_CHAIN_WORKFLOW["parameters"])
    if parameters is not None:
        params = parameters
    results = orchestrator.execute_workflow(
        {"services": services, "parameters": params}, job_id=job_id
    )
    called = [c.args[1] for c in orchestrator.call_mcp_tool.call_args_list]
    return results, called


@pytest.mark.parametrize(
    "services, message",
    [
        (["prepare_ligands"], "No ligand files available for preparation"),
        (["prepare_protein"], "No protein file available for preparation"),
        (["dock"], "No prepared protein available for docking"),
        (["visualize"], "No prepared protein available for visualization"),
    ],
)
def test_a_step_without_its_input_fails_the_job_with_a_clear_message(
    tmp_path, services, message
):
    results, called = _run(tmp_path, services)

    assert results["status"] == "failed"
    assert results["errors"] == [message]
    assert called == []
    log = (tmp_path / "pre" / "job.log").read_text(encoding="utf-8")
    assert message in log


def test_docking_without_prepared_ligands_fails_before_the_runner_is_called(tmp_path):
    results, called = _run(
        tmp_path,
        ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
        overrides={"prepare_ligands": lambda: {"prepared_ligands": [{"pdbqt_path": None}]}},
    )

    assert results["status"] == "failed"
    assert results["errors"] == ["No prepared ligands available for docking"]
    assert "run_docking" not in called


@pytest.mark.parametrize("missing", ["center", "box_size"])
def test_docking_without_a_box_names_the_box_not_a_key(tmp_path, missing):
    params = {k: v for k, v in _FULL_CHAIN_WORKFLOW["parameters"].items() if k != missing}

    results, called = _run(
        tmp_path,
        ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
        parameters=params,
    )

    assert results["status"] == "failed"
    assert results["errors"] == ["No docking box (center and box_size) set for docking"]
    assert "run_docking" not in called


@pytest.mark.parametrize(
    "prepared, message",
    [
        (
            {"prepared_proteins": [], "status": "failed",
             "errors": [{"protein": "/app/6lu7.pdb", "error": "No atoms read from 6lu7.pdb"}]},
            "Protein preparation returned no prepared receptor: No atoms read from 6lu7.pdb",
        ),
        (
            {"prepared_proteins": [{"pdbqt_path": None, "pdb_path": None}], "status": "ok"},
            "Protein preparation returned no prepared receptor",
        ),
    ],
)
def test_protein_preparation_with_no_receptor_fails_at_that_step(tmp_path, prepared, message):
    results, called = _run(
        tmp_path,
        ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        overrides={"prepare_proteins": lambda: prepared},
    )

    assert results["status"] == "failed"
    assert results["errors"] == [message]
    assert "prepare_protein" not in results["services_executed"]
    assert "run_docking" not in called


def test_visualize_with_no_docked_poses_is_skipped_and_the_job_completes(tmp_path):
    no_poses = lambda: {
        "results": [{"ligand_id": "ATP", "output_path": None, "error": "docking failed"}],
        "successful": 0,
    }

    results, called = _run(
        tmp_path, _FULL_CHAIN_WORKFLOW["services"], overrides={"run_docking": no_poses}
    )

    assert results["status"] == "completed"
    assert "convert_docked_poses" not in called
    viz = results["results"]["visualize"]
    assert viz["status"] == "skipped"
    assert viz["converted"] == []
    assert viz["receptor_pdb"] == "/app/prepared/6lu7_prepared.pdb"
    assert "visualize" in results["services_executed"]
    log = (tmp_path / "pre" / "job.log").read_text(encoding="utf-8")
    assert "visualize completed" in log and "0 ligand(s)" in log


# ---------------------------------------------------------------------------
# Ligands RCSB could not serve are reported, not silently dropped.
# ---------------------------------------------------------------------------

_ATPP_NOT_FOUND = {"ligand_id": "ATPP", "error": "Ligand ATPP not found on RCSB"}


def _download_with_failures(ligand_paths, failed):
    return lambda: {
        "protein_path": "/app/6lu7.pdb",
        "ligand_paths": ligand_paths,
        "failed_ligands": failed,
        "status": "ok",
    }


def test_a_ligand_that_could_not_be_downloaded_is_reported_and_the_rest_dock(tmp_path, caplog):
    caplog.set_level("INFO", logger="services.llm_orchestrator.orchestrator")
    from services.llm_orchestrator.orchestrator import skipped_ligand_notes

    params = {**_FULL_CHAIN_WORKFLOW["parameters"], "ligand_ids": ["ATP", "ATPP"]}
    results, called = _run(
        tmp_path,
        _FULL_CHAIN_WORKFLOW["services"],
        parameters=params,
        overrides={"download_structures": _download_with_failures(["/app/ATP.sdf"], [_ATPP_NOT_FOUND])},
    )

    assert results["status"] == "completed"
    assert results["errors"] == []
    assert "run_docking" in called
    assert results["results"]["get_input_files"]["failed_ligands"] == [_ATPP_NOT_FOUND]
    assert skipped_ligand_notes(results) == ["ATPP: Ligand ATPP not found on RCSB"]
    assert "1 ligand(s) could not be downloaded" in caplog.text
    log = (tmp_path / "pre" / "job.log").read_text(encoding="utf-8")
    assert "WARNING" in log and "ATPP: Ligand ATPP not found on RCSB" in log


def test_a_job_whose_every_ligand_failed_to_download_stops_at_the_download(tmp_path):
    failed = [_ATPP_NOT_FOUND, {"ligand_id": "XYZ", "error": "Failed to download ligand XYZ: read timed out"}]
    params = {**_FULL_CHAIN_WORKFLOW["parameters"], "ligand_ids": ["ATPP", "XYZ"]}
    results, called = _run(
        tmp_path,
        _FULL_CHAIN_WORKFLOW["services"],
        parameters=params,
        overrides={"download_structures": _download_with_failures([], failed)},
    )

    assert results["status"] == "failed"
    assert results["errors"] == [
        "No ligand could be downloaded from RCSB: ATPP: Ligand ATPP not found on RCSB; "
        "XYZ: Failed to download ligand XYZ: read timed out"
    ]
    assert called == ["download_structures"]


def test_a_skipped_ligand_is_still_reported_after_a_redock_that_does_not_download(tmp_path):
    from services.llm_orchestrator.orchestrator import skipped_ligand_notes

    params = {**_FULL_CHAIN_WORKFLOW["parameters"], "ligand_ids": ["ATP", "ATPP"]}
    overrides = {"download_structures": _download_with_failures(["/app/ATP.sdf"], [_ATPP_NOT_FOUND])}
    first, _ = _run(tmp_path, _FULL_CHAIN_WORKFLOW["services"], parameters=params, overrides=overrides)
    assert first["status"] == "completed"

    redock, called = _run(tmp_path, ["dock", "visualize"], parameters=params, overrides=overrides)

    assert "download_structures" not in called
    assert skipped_ligand_notes(redock) == ["ATPP: Ligand ATPP not found on RCSB"]


def test_skipped_ligand_notes_is_empty_without_a_download_step():
    from services.llm_orchestrator.orchestrator import skipped_ligand_notes

    assert skipped_ligand_notes(None) == []
    assert skipped_ligand_notes({"results": {"dock": {}}}) == []
    assert skipped_ligand_notes({"results": {"get_input_files": {"ligand_paths": []}}}) == []
