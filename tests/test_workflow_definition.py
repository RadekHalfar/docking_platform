"""Tests for workflow_definition module constants (B5 — CORE_PARAMS)."""

import pytest

from services.llm_orchestrator.workflow_definition import (
    CORE_PARAMS,
    DEFAULT_PARAMETERS,
    REQUIRED_PARAMETERS_BY_STEP,
    get_default_parameters,
)


def test_core_params_contains_expected_keys():
    """CORE_PARAMS must cover the four fundamental workflow parameters."""
    assert CORE_PARAMS == frozenset({"protein_id", "ligand_ids", "center", "box_size"})


def test_core_params_is_frozenset():
    """CORE_PARAMS must be immutable — calling add/discard should raise."""
    assert isinstance(CORE_PARAMS, frozenset)
    with pytest.raises(AttributeError):
        CORE_PARAMS.add("extra_param")  # type: ignore[attr-defined]


def test_core_params_subset_of_dock_step_requirements():
    """Every core parameter must appear among the dock step's required parameters."""
    dock_required = set(REQUIRED_PARAMETERS_BY_STEP.get("dock", []))
    assert CORE_PARAMS <= dock_required, (
        f"CORE_PARAMS has keys not in dock requirements: {CORE_PARAMS - dock_required}"
    )


def test_docking_algorithm_defaults_to_moldina():
    """Sessions/parameters without the key read as Moldina (PSO)."""
    assert DEFAULT_PARAMETERS["docking_algorithm"] == "moldina"
    assert get_default_parameters()["docking_algorithm"] == "moldina"
