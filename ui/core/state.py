"""The GUI's per-client state as a plain object (Phase 16, Stage 16.1).

``GuiSession`` holds everything the GUI's logic reads and writes -- what
Streamlit keeps in ``st.session_state``. Widget-local state (rename/delete
toggles, the revision selector, pose pickers, upload bookkeeping) stays in
each GUI framework; it is not logic state.

The Streamlit page keeps its state in ``st.session_state`` under the
existing keys and passes a view with these same attributes
(``ui.session_state.gui_session()``), so the ``ui/core`` functions run
unchanged against either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM
from services.llm_orchestrator.interactive_session import InteractiveSession
from services.llm_orchestrator.workflow_parser import WorkflowParser
from ui.worker_cap import WorkerCap, default_worker_count

GREETING_MESSAGE = {
    "role": "assistant",
    "content": (
        "Hello! Describe your docking workflow or use the form on the right.\n\n"
        "Example: *Download 6lu7 and dock ATP with center 10 20 30 box 25 30 35*"
    ),
}

DEFAULT_INPUT_MODE = "local_files"


def form_defaults(worker_cap: WorkerCap) -> Dict[str, Any]:
    """The parameter form's fields and their empty values. The keys are the
    Streamlit widget keys (``param_*``), kept as the form's field names so
    both GUIs share them. ``input_mode`` is not here: a Load resets every
    field below except the session name, but keeps the input mode."""
    return {
        "param_job_name": "",
        "param_protein_id": "",
        "param_ligand_ids_text": "",
        "param_ligand_folder_path": "",
        "param_center_x": "",
        "param_center_y": "",
        "param_center_z": "",
        "param_box_x": "",
        "param_box_y": "",
        "param_box_z": "",
        "param_ph": "7.4",
        "param_exhaustiveness": "8",
        "param_cpu": "1",
        "param_parallel": False,
        "param_max_workers": default_worker_count(worker_cap),
        "param_docking_algorithm": DEFAULT_DOCKING_ALGORITHM,
    }


@dataclass(frozen=True)
class Outcome:
    """What a GUI action asks of the page beyond the replies it appended to
    ``messages``: ``refresh`` -- redraw now (Streamlit: ``st.rerun()``);
    ``error`` -- show this next to the control, not in the chat."""

    refresh: bool = False
    error: Optional[str] = None


@dataclass
class GuiSession:
    """One browser client's GUI state. Never module-global (finding 8)."""

    worker_cap: WorkerCap
    messages: List[dict] = field(default_factory=lambda: [dict(GREETING_MESSAGE)])
    # Workflow parameters, incl. what the form doesn't show (upload paths,
    # chat-parsed values); the form is folded in before a run.
    params: Dict[str, Any] = field(default_factory=dict)
    # The parameter form's field values, keyed as form_defaults() + input_mode.
    form: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)
    llm_client: Any = None
    parser: WorkflowParser = field(default_factory=WorkflowParser)
    interactive_session: InteractiveSession = field(default_factory=InteractiveSession)
    # The docking run's OrchestratorBridge (kept after it finishes, for its result).
    bridge: Any = None
    # The "Load receptor for box picking" job's bridge, cleared once handled.
    box_picker_bridge: Any = None
    job_id: Optional[str] = None
    job_name: str = ""
    viewed_job_id: Optional[str] = None
    # The finished docking run has been handled (reply posted, results synced).
    job_done: bool = False
    # A staged redock awaiting confirmation: {"job_id", "steps", "params"}.
    pending_rerun: Optional[dict] = None
    comparison: Optional[dict] = None
    # A save-as name clash awaiting Overwrite / Save as: {"job_id", "name", "scope", "suggested"}.
    save_conflict: Optional[dict] = None
    box_picker_receptor_pdb: Optional[str] = None
    box_picker_last_click: Optional[dict] = None
    box_picker_error: Optional[str] = None
    box_picker_job_id: Optional[str] = None
    # A geometry chat command waiting for a receptor fetch it started.
    pending_geometry_target: Optional[str] = None

    def __post_init__(self) -> None:
        for key, default in {**self.form_defaults, "input_mode": DEFAULT_INPUT_MODE}.items():
            self.form.setdefault(key, default)

    @property
    def form_defaults(self) -> Dict[str, Any]:
        return form_defaults(self.worker_cap)
