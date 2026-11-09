"""Shared "which dict keys changed" helper for parameter diffing.

Used by :class:`~services.llm_orchestrator.workflow_state_manager.WorkflowStateManager`
(job.log parameter-change lines) and the GUI's own
redock-diff check -- one definition of "changed" instead of two
independently-maintained copies.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def changed_parameter_keys(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[str]:
    """Return, sorted, the keys whose value differs between *old* and *new*.

    A key present in only one of the two dicts counts as changed. ``old``
    may be ``None`` (no prior parameters), in which case every key in
    ``new`` is reported as changed.
    """
    old = old or {}
    return sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
