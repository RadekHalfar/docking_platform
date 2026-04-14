"""Interactive workflow session helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from .workflow_state_manager import WorkflowStateManager


@dataclass
class InteractiveSession:
    """Holds active session context for interactive CLI usage."""

    job_id: Optional[str] = None
    state_manager: Optional[WorkflowStateManager] = None
    last_results: Optional[Dict[str, Any]] = None
    session_active: bool = True
    pending_changed_params: Set[str] = field(default_factory=set)

    def has_active_job(self) -> bool:
        """Return True when a job is currently attached to this session."""
        return bool(self.job_id and self.state_manager)

    def activate(self, job_id: str, state_manager: WorkflowStateManager, last_results: Optional[Dict[str, Any]] = None) -> None:
        """Attach a job/state manager as the active session context."""
        self.job_id = job_id
        self.state_manager = state_manager
        self.last_results = last_results

    def clear(self) -> None:
        """Clear active job context."""
        self.job_id = None
        self.state_manager = None
        self.last_results = None
        self.pending_changed_params = set()

    def state_snapshot(self) -> Dict[str, Any]:
        """Return disk-backed current snapshot for active job."""
        if not self.has_active_job():
            return {
                "active": False,
                "job_id": None,
                "revision": None,
                "parameters": {},
                "runtime_state": {},
                "last_status": None,
            }

        assert self.state_manager is not None  # for type checkers

        return {
            "active": True,
            "job_id": self.job_id,
            "revision": self.state_manager.get_current_revision(),
            "parameters": self.state_manager.load_latest_parameters() or {},
            "runtime_state": self.state_manager.load_latest_state() or {},
            "last_status": (self.last_results or {}).get("status"),
        }

    def format_state_summary(self) -> str:
        """Return a human-friendly summary of current interactive session state."""
        snapshot = self.state_snapshot()
        if not snapshot["active"]:
            return "No active job in this session yet."

        lines = [
            "Session State:",
            f"  Job ID: {snapshot['job_id']}",
            f"  Revision: {snapshot['revision']}",
            f"  Last Status: {snapshot['last_status'] or 'unknown'}",
        ]

        params = snapshot["parameters"]
        if params:
            lines.append("  Parameters:")
            if "protein_id" in params:
                lines.append(f"    protein_id: {params['protein_id']}")
            if "ligand_ids" in params:
                lines.append(f"    ligand_ids: {params['ligand_ids']}")
            if "center" in params:
                lines.append(f"    center: {params['center']}")
            if "box_size" in params:
                lines.append(f"    box_size: {params['box_size']}")

        runtime_state = snapshot["runtime_state"]
        if runtime_state:
            lines.append("  Runtime State Keys: " + ", ".join(sorted(runtime_state.keys())))

        return "\n".join(lines)
