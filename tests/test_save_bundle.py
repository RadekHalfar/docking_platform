"""Tests for Phase 5: save_results named bundles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.llm_orchestrator.interactive_session import (
    save_results_bundle,
    validate_bundle_name,
)
from services.llm_orchestrator.workflow_llm_parser import _deterministic_intent


# ---------------------------------------------------------------------------
# validate_bundle_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "hem_box_a",
    "trial-1",
    "ATP.v2",
    "a",
    "A" * 64,
])
def test_validate_bundle_name_valid(name):
    assert validate_bundle_name(name) is None


@pytest.mark.parametrize("name,expected_fragment", [
    ("", "empty"),
    ("   ", "empty"),
    ("../evil", "path separators"),
    ("a/b", "path separators"),
    ("a\\b", "path separators"),
    ("-leading", "start with"),
    ("has space", "only"),
    ("checkpoints", "reserved"),
    ("visualization", "reserved"),
    ("docking_results", "reserved"),
])
def test_validate_bundle_name_invalid(name, expected_fragment):
    error = validate_bundle_name(name)
    assert error is not None
    assert expected_fragment.lower() in error.lower()


# ---------------------------------------------------------------------------
# save_results_bundle — filesystem behaviour
# ---------------------------------------------------------------------------

PARAMS = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [25.0, 25.0, 25.0]}


@pytest.fixture
def job_dir(tmp_path):
    """Create a minimal job directory with docking and visualization artifacts."""
    d = tmp_path / "job_test"
    d.mkdir()

    # Docking artifacts
    dock = d / "docking_results"
    dock.mkdir()
    (dock / "ATP_docked.pdbqt").write_text("ATOM 1 dummy\n")

    # Visualization artifacts
    viz = d / "visualization"
    viz.mkdir()
    (viz / "scene.png").write_bytes(b"\x89PNG dummy")

    # Docking box snapshot
    (d / "docking_box.json").write_text('{"center": [1, 2, 3]}')

    return d


def test_save_bundle_all_scope(job_dir):
    result = save_results_bundle(job_dir, "trial_a", "all", 5, PARAMS)

    assert result["success"] is True
    bundle = Path(result["bundle_path"])
    assert (bundle / "docking_results" / "ATP_docked.pdbqt").exists()
    assert (bundle / "visualization" / "scene.png").exists()
    assert (bundle / "docking_box.json").exists()
    assert "docking_results" in result["artifacts"]
    assert "visualization" in result["artifacts"]
    assert "docking_box.json" in result["artifacts"]

    # docking_box.json must reflect CURRENT parameters, not the stale disk file.
    with (bundle / "docking_box.json").open() as f:
        box = json.load(f)
    assert box["center"] == PARAMS["center"]
    assert box["box_size"] == PARAMS["box_size"]


def test_save_bundle_docking_box_from_params_overrides_stale_disk(tmp_path):
    """Bundle docking_box.json must use updated parameters even when disk file is stale."""
    job = tmp_path / "job_stale"
    job.mkdir()
    (job / "docking_results").mkdir()
    (job / "docking_results" / "ATP_docked.pdbqt").write_text("ATOM")
    # Stale disk file with old box
    stale_box = {"job_id": job.name, "center": [0.0, 0.0, 0.0], "box_size": [10.0, 10.0, 10.0], "source": "old"}
    (job / "docking_box.json").write_text(json.dumps(stale_box))

    updated_params = {**PARAMS, "center": [5.0, 6.0, 7.0], "box_size": [20.0, 20.0, 20.0]}
    result = save_results_bundle(job, "after_update", "docking", 9, updated_params)

    assert result["success"] is True
    with (Path(result["bundle_path"]) / "docking_box.json").open() as f:
        box = json.load(f)
    assert box["center"] == [5.0, 6.0, 7.0]
    assert box["box_size"] == [20.0, 20.0, 20.0]
    # source should indicate this came from parameters, not the stale disk entry
    assert box["source"] != "old"


def test_save_bundle_docking_only(job_dir):
    result = save_results_bundle(job_dir, "dock_only", "docking", 3, PARAMS)

    assert result["success"] is True
    bundle = Path(result["bundle_path"])
    assert (bundle / "docking_results").exists()
    assert not (bundle / "visualization").exists()
    assert "docking_results" in result["artifacts"]
    assert "visualization" not in result["artifacts"]


def test_save_bundle_visualization_only(job_dir):
    result = save_results_bundle(job_dir, "viz_only", "visualization", 4, PARAMS)

    assert result["success"] is True
    bundle = Path(result["bundle_path"])
    assert (bundle / "visualization").exists()
    assert not (bundle / "docking_results").exists()


def test_save_bundle_metadata_content(job_dir):
    result = save_results_bundle(job_dir, "meta_check", "all", 7, PARAMS)

    assert result["success"] is True
    meta_path = Path(result["bundle_path"]) / "metadata.json"
    assert meta_path.exists()

    with meta_path.open() as f:
        meta = json.load(f)

    assert meta["name"] == "meta_check"
    assert meta["source_revision"] == 7
    assert meta["scope"] == "all"
    assert meta["parameters"] == PARAMS
    assert meta["job_id"] == job_dir.name
    assert "timestamp" in meta
    assert isinstance(meta["artifacts"], list)


def test_save_bundle_overwrite_protection(job_dir):
    # First save succeeds.
    r1 = save_results_bundle(job_dir, "my_bundle", "all", 1, PARAMS)
    assert r1["success"] is True

    # Second save with same name and overwrite=False fails.
    r2 = save_results_bundle(job_dir, "my_bundle", "all", 2, PARAMS, overwrite=False)
    assert r2["success"] is False
    assert r2["error"] == "exists"


def test_save_bundle_overwrite_allowed(job_dir):
    save_results_bundle(job_dir, "replaceable", "all", 1, PARAMS)

    updated_params = {**PARAMS, "ligand_ids": ["HEM"]}
    r = save_results_bundle(job_dir, "replaceable", "all", 2, updated_params, overwrite=True)
    assert r["success"] is True

    meta_path = Path(r["bundle_path"]) / "metadata.json"
    with meta_path.open() as f:
        meta = json.load(f)
    assert meta["source_revision"] == 2
    assert meta["parameters"]["ligand_ids"] == ["HEM"]


def test_save_bundle_invalid_name_rejected(job_dir):
    result = save_results_bundle(job_dir, "../escape", "all", 1, PARAMS)
    assert result["success"] is False
    assert result["error"] is not None
    # No directory should have been created.
    assert not (job_dir / "saved_results" / "../escape").exists()


def test_save_bundle_no_artifacts_does_not_fail(tmp_path):
    """Bundle creation succeeds even when source artifact dirs are absent."""
    empty_job = tmp_path / "empty_job"
    empty_job.mkdir()

    result = save_results_bundle(empty_job, "empty_bundle", "all", 1, {})
    assert result["success"] is True
    assert result["artifacts"] == []
    assert (Path(result["bundle_path"]) / "metadata.json").exists()


# ---------------------------------------------------------------------------
# Intent parser — scope extraction (deterministic)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command,expected_scope", [
    ("save results as trial_a", "all"),
    ("save docking results as dock_v1", "docking"),
    ("save docking as dock_v2", "docking"),
    ("save visualization as viz_v1", "visualization"),
    ("save viz as vis2", "visualization"),
    ("save as quick_name", "all"),
])
def test_deterministic_save_scope(command, expected_scope):
    result = _deterministic_intent(command)
    assert result is not None
    assert result["intent"] == "save_results"
    assert result["scope"] == expected_scope


def test_deterministic_save_name_extraction():
    result = _deterministic_intent("save results as hem_box_a")
    assert result["name"] == "hem_box_a"


def test_deterministic_save_no_name():
    result = _deterministic_intent("save results")
    assert result["intent"] == "save_results"
    assert result["name"] is None
