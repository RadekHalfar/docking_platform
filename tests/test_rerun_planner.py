"""Tests for the dependency-aware partial rerun planner (Phase 3)."""
import pytest
from services.llm_orchestrator.rerun_planner import (
    STEP_ORDER,
    compute_rerun_steps,
    describe_rerun_plan,
    missing_required_params,
)
from services.llm_orchestrator.workflow_definition import (
    PARAM_INVALIDATION_MAP,
    WORKFLOW_STEPS,
)


# ---------------------------------------------------------------------------
# compute_rerun_steps — invalidation map tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "changed_params,expected_steps",
    [
        (set(), []),
        ({"ligand_ids"}, ["get_input_files", "prepare_ligands", "dock", "visualize"]),
        ({"center"}, ["dock", "visualize"]),
        ({"box_size"}, ["dock", "visualize"]),
        ({"protein_id"}, ["get_input_files", "prepare_protein", "dock", "visualize"]),
        ({"exhaustiveness"}, ["dock", "visualize"]),
    ],
)
def test_compute_rerun_steps_map(changed_params, expected_steps):
    """Test parameter-to-steps invalidation mapping."""
    steps = compute_rerun_steps(changed_params)
    assert steps == expected_steps


def test_cpu_change_invalidates_dock_and_visualize():
    """cpu affects dock and its downstream visualize."""
    steps = compute_rerun_steps({"cpu"})
    assert "dock" in steps
    assert "visualize" in steps


def test_ph_change_cascades_through_preparation_dock_viz():
    steps = compute_rerun_steps({"ph"})
    assert "prepare_ligands" in steps
    assert "prepare_protein" in steps
    assert "dock" in steps
    assert "visualize" in steps


def test_multiple_params_union():
    steps = compute_rerun_steps({"center", "ligand_ids"})
    assert steps == ["get_input_files", "prepare_ligands", "dock", "visualize"]


def test_unknown_param_yields_empty():
    steps = compute_rerun_steps({"nonexistent_param"})
    assert steps == []


# ---------------------------------------------------------------------------
# Intersection with original_services
# ---------------------------------------------------------------------------

def test_intersection_with_original_services():
    """Only steps that were originally executed should be planned."""
    steps = compute_rerun_steps(
        {"ligand_ids"},
        original_services=["get_input_files", "prepare_ligands", "dock"],
    )
    assert steps == ["get_input_files", "prepare_ligands", "dock"]
    assert "visualize" not in steps


def test_protein_unchanged_not_in_rerun_for_ligand_change():
    """When only ligand_ids changed, prepare_protein should NOT be invalidated."""
    steps = compute_rerun_steps({"ligand_ids"})
    assert "prepare_protein" not in steps


def test_full_workflow_rerun_when_all_key_params_change():
    steps = compute_rerun_steps({"protein_id", "ligand_ids", "center", "box_size"})
    assert steps == STEP_ORDER


def test_step_order_matches_canonical_workflow_definition():
    """Planner ordering must match centralized workflow definition."""
    assert STEP_ORDER == WORKFLOW_STEPS


def test_all_invalidation_targets_are_known_steps():
    """Central invalidation map should only reference canonical steps."""
    known_steps = set(WORKFLOW_STEPS)
    for invalidated_steps in PARAM_INVALIDATION_MAP.values():
        assert set(invalidated_steps).issubset(known_steps)


# ---------------------------------------------------------------------------
# describe_rerun_plan
# ---------------------------------------------------------------------------

def test_describe_empty():
    text = describe_rerun_plan([])
    assert "No steps" in text


def test_describe_non_empty():
    text = describe_rerun_plan(["dock", "visualize"])
    assert "Run docking" in text
    assert "Generate visualization" in text
    assert "1." in text
    assert "2." in text


# ---------------------------------------------------------------------------
# missing_required_params — shared by the CLI (_execute_partial_rerun) and
# the GUI (_handle_redock_request), see docs/reviews/stage_11_2_11_3_r2.md
# ---------------------------------------------------------------------------

def test_missing_required_params_none_missing():
    params = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1, 2, 3], "box_size": [20, 20, 20]}
    assert missing_required_params(["dock"], params) == []


def test_missing_required_params_reports_step_name():
    missing = missing_required_params(["dock"], {"protein_id": "6lu7"})
    assert any("center" in m and "'dock'" in m for m in missing)
    assert any("ligand_ids" in m and "'dock'" in m for m in missing)


def test_missing_required_params_falsy_values_count_as_missing():
    params = {"protein_id": "", "ligand_ids": [], "center": [1, 2, 3], "box_size": [20, 20, 20]}
    missing = missing_required_params(["dock"], params)
    assert any("protein_id" in m for m in missing)
    assert any("ligand_ids" in m for m in missing)


def test_missing_required_params_union_across_steps():
    missing = missing_required_params(["prepare_ligands", "prepare_protein"], {})
    assert any("ligand_ids" in m and "'prepare_ligands'" in m for m in missing)
    assert any("protein_id" in m and "'prepare_protein'" in m for m in missing)


def test_missing_required_params_empty_steps():
    assert missing_required_params([], {}) == []


@pytest.mark.parametrize(
    "file_inputs",
    [
        {"protein_file": "/x/receptor.pdb", "ligand_files": ["/x/lig.sdf"]},
        {"protein_file": "/x/receptor.pdb", "ligand_folder": "/x/ligands"},
    ],
)
def test_missing_required_params_accepts_local_file_inputs(file_inputs):
    # GUI "Local Files" / "Batch Processing" sessions have no
    # protein_id/ligand_ids at all -- reported 2026-10-01 as "Cannot redock
    # -- required parameters are missing: ligand_ids ..., protein_id ...".
    params = {**file_inputs, "center": [1, 2, 3], "box_size": [20, 20, 20]}
    assert missing_required_params(WORKFLOW_STEPS, params) == []


def test_missing_required_params_empty_local_file_list_still_missing():
    params = {"protein_file": "/x/receptor.pdb", "ligand_files": [], "center": [1, 2, 3], "box_size": [20, 20, 20]}
    missing = missing_required_params(["dock"], params)
    assert any("ligand_ids" in m for m in missing)


@pytest.mark.parametrize(
    "file_param, id_param",
    [("protein_file", "protein_id"), ("ligand_files", "ligand_ids"), ("ligand_folder", "ligand_ids")],
)
def test_local_file_change_reruns_same_steps_as_its_id_counterpart(file_param, id_param):
    assert compute_rerun_steps({file_param}) == compute_rerun_steps({id_param})


def test_changed_ligand_files_reruns_ligand_preparation():
    steps = compute_rerun_steps({"ligand_files"}, original_services=WORKFLOW_STEPS)
    assert steps == ["get_input_files", "prepare_ligands", "dock", "visualize"]
