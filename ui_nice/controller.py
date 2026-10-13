"""One browser client's page controller (Phase 16, Stage 16.4).

It holds the client's :class:`~ui.core.state.GuiSession` and what every
action needs besides it (``output_base``, the bridge class), runs the live
progress timer, and redraws the views after anything changes state.

Each event handler calls a ``ui/core`` action and then :meth:`sync`, which
asks every view to bring its widgets in line with the state. The views
update only what changed, so a redraw costs little: this is NiceGUI's
equivalent of Streamlit's rerun.

All clients share one event loop, so slow file work runs off it
(``run.io_bound``): the views' timer reads (:meth:`View.poll`), a geometry
command's receptor read (:meth:`apply_geometry`) and a results bundle copy
(:meth:`start_save`, from the chat, the Results tab's panel or its conflict
buttons). Review Stage R2 measured those last two at up to 80 ms and 37 s on
a Docker Desktop bind mount. A save, and a session's delete (Stage 16.7),
hold :attr:`PageController.busy` while they run.

When a run finishes, completed or failed, the page switches to the Results
tab (:attr:`PageController.show_results`); a stopped run leaves it where it is.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional, Protocol

from nicegui import background_tasks, run

from ui import orchestrator_bridge
from ui.core import box_picker, run_control, save_results
from ui.core.state import GuiSession, Outcome, reply

logger = logging.getLogger(__name__)

# How often a client's timer checks its background jobs. Tests shorten it.
POLL_INTERVAL_S = 1.0

# controller.busy while a save copies the results, or a session is deleted.
SAVING = "Saving results..."
DELETING = "Deleting session..."
# Why the actions that change the session are off while busy.
_BUSY_REASONS = {
    SAVING: "Results are being saved — wait for that",
    DELETING: "A session is being deleted — wait for that",
}

# The finished runs that switch the page to the Results tab.
SHOW_RESULTS_ON = ("completed", "failed")


class View(Protocol):
    def sync(self) -> None:
        """Bring the widgets in line with the state. No file I/O: a sync
        runs on every event, on the shared event loop."""

    async def poll(self) -> None:
        """Re-read what the view shows from disk, off the event loop. The
        timer calls it before a sync while a job is running."""


class PageController:
    def __init__(self, state: GuiSession, output_base: Path) -> None:
        self.state = state
        self.output_base = Path(output_base)
        self.upload_dir = self.output_base / "uploads"
        # An action's Outcome.error, shown next to the action buttons until
        # the next action.
        self.error: Optional[str] = None
        # Why the box picker's last Load didn't start, (message, severity),
        # shown under its button until the next action.
        self.picker_message: Optional[tuple[str, str]] = None
        # What runs in the background (SAVING, DELETING), or None. While
        # set, the chat input and the actions that would change the session
        # it works on are off: Run, New Session and Reset, and the Sessions
        # tab's Load, Delete and Restore revision (blocked_reason) -- else a
        # Delete during a save removes the job dir under the copy, and a Load
        # swaps the session it reports to (Review Stage R2, Deferred #3).
        self.busy: Optional[str] = None
        # True while views write the state into their widgets: NiceGUI fires
        # a widget's change handler on a programmatic change too, and the
        # handlers ignore those (the state already holds the value).
        self.syncing = False
        self._views: list[View] = []
        # Switches the page to the Results tab (set by the layout).
        self.show_results: Optional[Callable[[], None]] = None
        # Geometry commands are applied one at a time, in the order sent.
        self._geometry_lock = asyncio.Lock()

    def bridge_factory(self):
        """A new docking bridge. Looked up at call time, so a test that swaps
        ``ui.orchestrator_bridge.OrchestratorBridge`` reaches it."""
        return orchestrator_bridge.OrchestratorBridge()

    def add_view(self, view: View) -> None:
        self._views.append(view)

    def sync(self) -> None:
        self.syncing = True
        try:
            for view in self._views:
                view.sync()
        finally:
            self.syncing = False

    def blocked_reason(self) -> Optional[str]:
        """Why the actions that change or replace the session are off -- a
        run going in this client, or background work (:attr:`busy`) -- or None."""
        if run_control.active_bridge_job_id(self.state) is not None:
            return "A session is running — Stop it"
        if self.busy is not None:
            return _BUSY_REASONS[self.busy]
        return None

    def start_action(self) -> None:
        """Called first by every action handler: an earlier action's
        messages are cleared, as Streamlit drops them on the next run."""
        self.error = None
        self.picker_message = None

    def show_outcome(self, outcome: Outcome) -> None:
        if outcome.error:
            self.error = outcome.error
        self.sync()

    # ── The background jobs ─────────────────────────────────────────────────

    def process(self) -> bool:
        """Fold the background jobs' progress and outcome into the state:
        the docking run's log and finish, and the box picker's receptor
        prep (which a geometry chat command can start). Returns whether the
        state changed."""
        changed = run_control.process_docking_bridge(self.state)
        if box_picker.process_box_picker_bridge(self.state, self.output_base, apply_geometry=self.apply_geometry):
            changed = True
        return changed

    async def tick(self) -> None:
        """The timer: redraw while a job runs, and whenever processing it
        changed the state -- including a job that ended before any tick saw
        it running."""
        was_done = self.state.job_done
        changed = self.process()
        if self.state.job_done and not was_done:
            self._show_finished_run()
        if not (changed or run_control.any_bridge_running(self.state)):
            return
        for view in self._views:
            await view.poll()
        self.sync()

    def _show_finished_run(self) -> None:
        bridge = self.state.bridge
        status = "failed" if bridge.get_error() else (bridge.get_result() or {}).get("status")
        if status in SHOW_RESULTS_ON and self.show_results is not None:
            self.show_results()

    # ── Slow commands, run off the event loop ────────────────────────────────

    def apply_geometry(self, state: GuiSession, receptor_path: Path, target: str) -> None:
        """``ui.core`` 's *apply_geometry*: read the receptor off the event
        loop, then apply the geometry and redraw."""
        background_tasks.create(self._apply_geometry(Path(receptor_path), target), name="protein geometry")

    async def _apply_geometry(self, receptor_path: Path, target: str) -> None:
        async with self._geometry_lock:
            try:
                center, box_size = await run.io_bound(box_picker.read_protein_geometry, receptor_path)
            except (OSError, ValueError) as exc:
                box_picker.geometry_failed(self.state, exc)
            else:
                box_picker.set_protein_geometry(self.state, target, center, box_size)
        self.sync()

    def run_save(self, intent: dict) -> None:
        """``ui.core.chat`` 's *run_save*: check the save now, copy the
        bundle off the event loop, then reply."""
        self.start_save(*save_results.plan_chat_save(self.state, self.output_base, intent))

    def start_save(self, message: Optional[str], job: Optional[save_results.SaveJob]) -> None:
        """Run a save that ``ui.core.save_results`` planned: the copy off the
        event loop, then the reply. Without a job, *message* (if any) is the
        reply: why it couldn't be planned."""
        if job is None:
            if message:
                reply(self.state, message)
            return
        self.busy = SAVING
        background_tasks.create(self._save(job), name="save results")

    async def _save(self, job: save_results.SaveJob) -> None:
        try:
            result = await run.io_bound(save_results.write_bundle, job)
            reply(self.state, save_results.finish_save(self.state, job, result))
        except Exception as exc:
            # Background-task boundary: save_results_bundle reports file
            # errors itself, so this is anything else (e.g. unreadable
            # checkpoints). Without it the user would get no reply at all.
            logger.exception("save results as %r failed", job.name)
            reply(self.state, f"Save failed: {exc}")
        finally:
            self.busy = None
            self.sync()
