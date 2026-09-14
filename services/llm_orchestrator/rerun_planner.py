"""Dependency-aware partial rerun planner.

Given a set of changed parameter keys, compute the minimal ordered list of
workflow steps that must be re-executed.
"""
from __future__ import annotations

from typing import List, Optional, Set

from services.llm_orchestrator.workflow_definition import (
    PARAM_INVALIDATION_MAP,
    REQUIRED_PARAMETERS_BY_STEP,
    STEP_LABELS,
    WORKFLOW_STEPS,
)

# Backward-compatible exported alias.
STEP_ORDER: List[str] = WORKFLOW_STEPS


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
        invalidated |= PARAM_INVALIDATION_MAP.get(param, set())

    if original_services is not None:
        allowed = set(original_services)
        invalidated &= allowed

    return [s for s in STEP_ORDER if s in invalidated]


def missing_required_params(steps: List[str], params: dict) -> List[str]:
    """Return "param (required by 'step')" strings for each required
    parameter missing/falsy in ``params``, across all given steps.

    Shared by the CLI's ``_execute_partial_rerun`` (nlp_orchestrator.py) and
    the GUI's ``_handle_redock_request`` (ui/app.py) so a fix to what counts
    as "missing" for a rerun only has to be made in one place (see
    docs/reviews/stage_11_2_11_3_r2.md).
    """
    missing: List[str] = []
    for step in steps:
        for param in REQUIRED_PARAMETERS_BY_STEP.get(step, []):
            if not params.get(param):
                missing.append(f"{param} (required by '{step}')")
    return missing


def describe_rerun_plan(steps: List[str]) -> str:
    """Return a human-readable summary of the planned rerun."""
    if not steps:
        return "No steps need to be re-executed."

    lines = ["Rerun plan:"]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {STEP_LABELS.get(step, step)}")
    return "\n".join(lines)
