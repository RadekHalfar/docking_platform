"""Tests for the dependency-aware partial rerun planner (Phase 3)."""
from services.llm_orchestrator.rerun_planner import (
    STEP_ORDER,
    compute_rerun_steps,
    describe_rerun_plan,
)
from services.llm_orchestrator.workflow_definition import (
    PARAM_INVALIDATION_MAP,
    WORKFLOW_STEPS,
)


# ---------------------------------------------------------------------------
# compute_rerun_steps — invalidation map tests
# ---------------------------------------------------------------------------

def test_no_changes_yields_empty_plan():
    assert compute_rerun_steps(set()) == []


def test_ligand_change_invalidates_download_prep_dock_viz():
    steps = compute_rerun_steps({"ligand_ids"})
    assert steps == ["download", "prepare_ligands", "dock", "visualize"]


def test_center_change_invalidates_dock_and_viz_only():
    steps = compute_rerun_steps({"center"})
    assert steps == ["dock", "visualize"]


def test_box_size_change_invalidates_dock_and_viz_only():
    steps = compute_rerun_steps({"box_size"})
    assert steps == ["dock", "visualize"]


def test_protein_change_invalidates_download_prep_protein_dock_viz():
    steps = compute_rerun_steps({"protein_id"})
    assert steps == ["download", "prepare_protein", "dock", "visualize"]


def test_exhaustiveness_change_invalidates_dock_and_viz():
    steps = compute_rerun_steps({"exhaustiveness"})
    assert steps == ["dock", "visualize"]


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
    assert steps == ["download", "prepare_ligands", "dock", "visualize"]


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
        original_services=["download", "prepare_ligands", "dock"],
    )
    assert steps == ["download", "prepare_ligands", "dock"]
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
