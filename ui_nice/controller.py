"""One browser client's page controller (Phase 16, Stage 16.4).

It holds the client's :class:`~ui.core.state.GuiSession` and what every
action needs besides it (``output_base``, the bridge class), runs the live
progress timer, and redraws the views after anything changes state.

Each event handler calls a ``ui/core`` action and then :meth:`sync`, which
asks every view to bring its widgets in line with the state. The views
update only what changed, so a redraw costs little: this is NiceGUI's
equivalent of Streamlit's rerun.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol

from ui import orchestrator_bridge
from ui.core import box_picker, run_control
from ui.core.state import GuiSession, Outcome

# How often a client's timer checks its background jobs. Tests shorten it.
POLL_INTERVAL_S = 1.0


class View(Protocol):
    def sync(self) -> None:
        """Bring the widgets in line with the state."""


class PageController:
    def __init__(self, state: GuiSession, output_base: Path) -> None:
        self.state = state
        self.output_base = Path(output_base)
        self.upload_dir = self.output_base / "uploads"
        # An action's Outcome.error, shown next to the action buttons until
        # the next action.
        self.error: Optional[str] = None
        self._views: list[View] = []
        # A bridge was running at the last tick: poll once more, so a job
        # that ended during that tick is handled (cf. Streamlit's
        # bridge_was_running).
        self._poll_once_more = False

    def bridge_factory(self):
        """A new docking bridge. Looked up at call time, so a test that swaps
        ``ui.orchestrator_bridge.OrchestratorBridge`` reaches it."""
        return orchestrator_bridge.OrchestratorBridge()

    def add_view(self, view: View) -> None:
        self._views.append(view)

    def sync(self) -> None:
        for view in self._views:
            view.sync()

    def start_action(self) -> None:
        """Called first by every action handler: an earlier action's error
        is cleared, as Streamlit drops an inline error on the next run."""
        self.error = None

    def show_outcome(self, outcome: Outcome) -> None:
        if outcome.error:
            self.error = outcome.error
        self.sync()

    def tick(self) -> None:
        """Fold the background jobs' progress and outcome into the state:
        the docking run's log and finish, and the box picker's receptor
        prep (which a geometry chat command can start). Redraws only while
        a job is running, and once more after it ended."""
        running = run_control.any_bridge_running(self.state)
        # A receptor prep is dropped once processed, so one still here is
        # news even if it finished before any tick saw it running.
        prep_pending = self.state.box_picker_bridge is not None
        run_control.process_docking_bridge(self.state)
        box_picker.process_box_picker_bridge(self.state, self.output_base)
        active = running or prep_pending or self._poll_once_more
        self._poll_once_more = running or run_control.any_bridge_running(self.state)
        if active:
            self.sync()
