"""Dependency-aware partial rerun planner.

Given a set of changed parameter keys, compute the minimal ordered list of
workflow steps that must be re-executed.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set

# Canonical step execution order.
STEP_ORDER: List[str] = [
    "download",
    "prepare_ligands",
    "prepare_protein",
    "dock",
    "visualize",
]

# For each parameter, the set of steps that are *directly* invalidated when
# that parameter changes.  Downstream propagation is handled separately.
_PARAM_INVALIDATION: Dict[str, FrozenSet[str]] = {
    "protein_id": frozenset({"download", "prepare_protein", "dock", "visualize"}),
    "ligand_ids": frozenset({"download", "prepare_ligands", "dock", "visualize"}),
    "center": frozenset({"dock", "visualize"}),
    "box_size": frozenset({"dock", "visualize"}),
    # Docking-engine tunables only invalidate the docking step and its
    # downstream dependents.
    "ph": frozenset({"prepare_ligands", "prepare_protein", "dock", "visualize"}),
    "exhaustiveness": frozenset({"dock", "visualize"}),
    "cpu": frozenset({"dock", "visualize"}),
    "population": frozenset({"dock", "visualize"}),
    "num_modes": frozenset({"dock", "visualize"}),
    "energy_range": frozenset({"dock", "visualize"}),
}

# Direct upstream dependencies: a step can only run if its prerequisites have
# valid results.  Used to ensure we never skip a required prerequisite.
_STEP_UPSTREAM: Dict[str, FrozenSet[str]] = {
    "download": frozenset(),
    "prepare_ligands": frozenset({"download"}),
    "prepare_protein": frozenset({"download"}),
    "dock": frozenset({"prepare_ligands", "prepare_protein"}),
    "visualize": frozenset({"dock"}),
}


def compute_rerun_steps(
    changed_params: Set[str],
    original_services: Optional[List[str]] = None,
) -> List[str]:
    """Return the minimal ordered list of steps to re-execute.

    Parameters
    ----------
    changed_params:
        Set of parameter keys that changed (e.g. ``{"ligand_ids"}``).
    original_services:
        The full list of services from the last executed workflow.
        If provided, the result is intersected with this list so that we
        never run a service the user did not originally request.

    Returns
    -------
    Ordered list of step names in execution order.
    """
    if not changed_params:
        return []

    invalidated: Set[str] = set()
    for param in changed_params:
        invalidated |= _PARAM_INVALIDATION.get(param, set())

    if original_services is not None:
        allowed = set(original_services)
        invalidated &= allowed

    return [s for s in STEP_ORDER if s in invalidated]


def describe_rerun_plan(steps: List[str]) -> str:
    """Return a human-readable summary of the planned rerun."""
    if not steps:
        return "No steps need to be re-executed."

    labels = {
        "download": "Download structures",
        "prepare_ligands": "Prepare ligands",
        "prepare_protein": "Prepare protein",
        "dock": "Run docking",
        "visualize": "Generate visualization",
    }
    lines = ["Rerun plan:"]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {labels.get(step, step)}")
    return "\n".join(lines)
