"""The Sessions tab (Phase 16, Stage 16.7): the active session's revision
history with Restore, Session History (Load, Rename, Delete, Compare) and
Failed Sessions -- Streamlit's sidebar lists and its "Revision history"
expander.

Unlike Streamlit's 10 newest, every session is listed (owner's decision,
2026-10-13).

:meth:`SessionsView.sync` touches no file, and does nothing while the tab
is hidden. The listing and the revision history are read off the event
loop (``run.io_bound``) in one trip (:meth:`SessionsView.start_refresh`):
when the tab is opened, after this tab's own actions, on Refresh, and while
the tab is shown, when the session changes in a way that can change them
(:meth:`SessionsView._change_key`). The listing parses every session's
results file, 4-6 s for ~170 sessions on a Docker Desktop bind mount, so on
a change it is re-read only when a run finishes or the active job changes
(:meth:`SessionsView._listing_key`); a chat reply (Save Session and
``undo`` post one) re-reads only the revisions (Review Stage R3). A
delete's ``rmtree`` runs off the loop too (finding 7), holding
``controller.busy``.

Load, Delete and Restore revision are off while a run goes in this client
or the controller is busy with a save or a delete (Rename only for the
latter)
(:meth:`~ui_nice.controller.PageController.blocked_reason`; Review Stage
R2, Deferred #3), except that a Delete of another session is allowed while
a run goes, as in Streamlit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from nicegui import background_tasks, run, ui

from services.llm_orchestrator.job_comparator import compare_jobs
from ui.core import results_view, run_control, sessions
from ui.core.state import reply
from ui.job_history import JOB_NAME_MAX_CHARS
from ui_nice.controller import DELETING, PageController
from ui_nice.widgets import markdown

logger = logging.getLogger(__name__)

NO_COMPLETED = "No completed sessions yet."
NO_FAILED = "No failed or cancelled sessions."
LISTING = "Reading the sessions..."
RUNNING_HERE = "Running in this browser tab — use Stop (on the Setup tab) before deleting."
RESTORE_TIP = (
    "Restore that revision's parameters (recorded as a new revision); Run Docking then reruns with them."
)


@dataclass
class _Listing:
    """What one refresh read off the event loop."""

    completed: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    # The active session's revision panel: (history markdown, current
    # revision, restorable revisions newest first), or None without one.
    revisions: Optional[tuple[str, Optional[int], list[int]]] = None


def _read_listing(output_base: Path, state_manager, previous: Optional[_Listing] = None) -> _Listing:
    """The sessions and the revisions; with *previous*, only the revisions
    (its sessions are kept)."""
    if previous is None:
        completed, failed = sessions.list_sessions(output_base)
    else:
        completed, failed = previous.completed, previous.failed
    revisions = sessions.revision_panel_data(state_manager) if state_manager is not None else None
    return _Listing(completed, failed, revisions)


@dataclass
class _Row:
    """One session row's widgets that sync() updates."""

    load: ui.button
    load_tip: ui.tooltip
    rename: ui.button
    delete: ui.button
    running: ui.label
    editor: ui.row
    name_input: ui.input
    rename_save: ui.button


class SessionsView:
    def __init__(
        self,
        controller: PageController,
        *,
        on_load: Callable[[], None],
        on_compare: Callable[[], None],
    ) -> None:
        self.controller = controller
        self.state = controller.state
        # Called after a Load (switch to Results) and a Compare (to Compare Sessions).
        self._on_loaded = on_load
        self._on_compared = on_compare
        # Whether the Sessions tab is the open one (set by the layout).
        self.shown = False
        # The last refresh's result, and the state manager its revisions
        # were read for; None before the first refresh.
        self._listing: Optional[_Listing] = None
        self._listing_manager = None
        # The change key last refreshed for, the listing key the sessions
        # were last read for, and the current refresh's number (a later
        # refresh makes an earlier one stale).
        self._refresh_key: Optional[tuple] = None
        self._listed_key: Optional[tuple] = None
        self._generation = 0
        self.refreshing = False
        # The listing drawn, by identity.
        self._drawn: Optional[_Listing] = None
        self._rows: dict[str, _Row] = {}
        # Compare checkboxes ticked, by job id; the row being renamed; the
        # session the delete dialog asks about.
        self._selected: set[str] = set()
        self._renaming: Optional[str] = None
        self._deleting: Optional[dict] = None
        # This tab's last action error, until its next action; the last
        # read's error, until a read succeeds.
        self.error: Optional[str] = None
        self.read_error: Optional[str] = None

        with ui.column().classes("w-full p-2 gap-2"):
            with ui.row().classes("w-full items-center"):
                ui.label("Sessions").classes("text-lg font-medium")
                ui.space()
                self.spinner = ui.spinner(size="sm")
                ui.button("Refresh", icon="refresh", on_click=self._on_refresh).props("flat dense").mark(
                    "sessions-refresh"
                )
            self.error_label = ui.label().classes("text-negative").mark("sessions-error")
            self._build_revision_panel()
            ui.label("Session History").classes("font-medium")
            self.completed_list = ui.column().classes("w-full gap-1").mark("session-history")
            with ui.row().classes("items-center"):
                self.compare_button = ui.button(
                    "Compare selected sessions", on_click=self._on_compare
                ).props("outline").mark("compare-sessions")
            ui.separator()
            ui.label("Failed Sessions").classes("font-medium")
            self.failed_list = ui.column().classes("w-full gap-1").mark("failed-sessions")
        self._build_delete_dialog()

        controller.add_view(self)
        self.sync()

    # ── Building ─────────────────────────────────────────────────────────────

    def _build_revision_panel(self) -> None:
        with ui.card().classes("w-full").mark("active-session") as self.active_card:
            self.active_label = markdown().mark("active-session-label")
            with ui.expansion("Revision history", value=True).classes("w-full").mark("revision-history"):
                self.revision_table = markdown().mark("revision-table")
                self.revision_current = ui.label().classes("text-caption").mark("revision-current")
                with ui.row().classes("items-center") as self.restore_row:
                    self.revision_select = ui.select([], label="Revision").classes("min-w-32").mark(
                        "revision-select"
                    )
                    self.restore_button = ui.button("Restore revision", on_click=self._on_restore).mark(
                        "restore-revision"
                    )
                    with self.restore_button:
                        self.restore_tip = ui.tooltip()

    def _build_delete_dialog(self) -> None:
        with ui.dialog().mark("delete-dialog") as self.delete_dialog, ui.card():
            self.delete_message = markdown().mark("delete-message")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._on_delete_cancel).props("flat").mark("delete-cancel")
                ui.button("Confirm delete", on_click=self._on_delete_confirm).props("color=negative").mark(
                    "delete-confirm"
                )

    def _draw_rows(self, listing: _Listing) -> None:
        # A name being typed survives the redraw (a run finishing re-lists).
        typed = self._rows[self._renaming].name_input.value if self._renaming in self._rows else None
        self._rows = {}
        for container, jobs, detail, selectable, empty in (
            (self.completed_list, listing.completed, sessions.completed_session_detail, True, NO_COMPLETED),
            (self.failed_list, listing.failed, sessions.failed_session_detail, False, NO_FAILED),
        ):
            container.clear()
            with container:
                if not jobs:
                    ui.label(empty).classes("text-caption text-grey-7")
                for job in jobs:
                    self._rows[job["job_id"]] = self._draw_row(job, detail(job), selectable=selectable)
        listed = {job["job_id"] for job in listing.completed}
        self._selected &= listed
        if self._renaming not in self._rows:
            self._renaming = None
        elif typed is not None:
            self._rows[self._renaming].name_input.value = typed
        self._drawn = listing

    def _draw_row(self, job: dict, detail: str, *, selectable: bool) -> _Row:
        job_id = job["job_id"]
        with ui.column().classes("w-full gap-0").mark(f"session-row-{job_id}"):
            with ui.row().classes("w-full items-center no-wrap"):
                if selectable:
                    ui.checkbox(
                        value=job_id in self._selected,
                        on_change=lambda e, j=job_id: self._on_select(j, e.value),
                    ).mark(f"select-{job_id}")
                markdown(sessions.session_row_label(job, detail)).classes("grow")
                load = ui.button("Load", on_click=lambda j=job: self._on_load(j)).props("dense").mark(
                    f"load-{job_id}"
                )
                with load:
                    load_tip = ui.tooltip()
                rename = ui.button("Rename", on_click=lambda j=job: self._on_rename_toggle(j)).props(
                    "dense outline"
                ).mark(f"rename-{job_id}")
                delete = ui.button("Delete", on_click=lambda j=job: self._on_delete(j)).props(
                    "dense outline color=negative"
                ).mark(f"delete-{job_id}")
            running = ui.label(RUNNING_HERE).classes("text-caption").mark(f"running-{job_id}")
            with ui.row().classes("items-center no-wrap").mark(f"rename-editor-{job_id}") as editor:
                name_input = ui.input("New name", value=job.get("job_name") or "").props(
                    f"maxlength={JOB_NAME_MAX_CHARS} dense"
                ).mark(f"rename-input-{job_id}")
                rename_save = ui.button("Save", on_click=lambda j=job: self._on_rename_save(j)).props(
                    "dense"
                ).mark(f"rename-save-{job_id}")
                ui.button("Cancel", on_click=self._on_rename_cancel).props("dense flat").mark(
                    f"rename-cancel-{job_id}"
                )
        return _Row(load, load_tip, rename, delete, running, editor, name_input, rename_save)

    # ── Sync (state -> widgets) ──────────────────────────────────────────────

    def sync(self) -> None:
        if not self.shown:
            return  # set_shown() syncs on opening
        if self._change_key() != self._refresh_key:
            self.start_refresh(relist=False)
        listing = self._listing
        self.spinner.set_visibility(self.refreshing)
        if listing is not None and listing is not self._drawn:
            self._draw_rows(listing)
        elif listing is None and self._drawn is None and not self.completed_list.default_slot.children:
            with self.completed_list:
                ui.label(LISTING).classes("text-caption text-grey-7")
        error = self.error or self.read_error
        self.error_label.set_text(error or "")
        self.error_label.set_visibility(bool(error))
        self._sync_rows()
        self._sync_revision_panel()

    async def poll(self) -> None:
        pass  # reads on its own triggers (see the module docstring)

    def _sync_rows(self) -> None:
        blocked = self.controller.blocked_reason()
        running_id = run_control.active_bridge_job_id(self.state)
        busy = self.controller.busy is not None
        for job_id, row in self._rows.items():
            row.load.set_enabled(blocked is None)
            row.load_tip.set_text(
                "Load this session's results and parameters." if blocked is None
                else f"{blocked} before loading another."
            )
            row.delete.set_enabled(job_id != running_id and not busy)
            # Not during a delete, whose rmtree a rename would write into.
            row.rename.set_enabled(not busy)
            row.rename_save.set_enabled(not busy)
            row.running.set_visibility(job_id == running_id)
            row.editor.set_visibility(job_id == self._renaming)
        self.compare_button.set_visibility(len(self._selected) >= 2)
        self.compare_button.set_enabled(not busy)

    def _sync_revision_panel(self) -> None:
        state = self.state
        session = state.interactive_session
        active = session.has_active_job()
        self.active_card.set_visibility(active)
        if not active:
            return
        self.active_label.set_content(f"Active session: {results_view.job_label(state.job_id, state.job_name)}")
        listing = self._listing
        revisions = (
            listing.revisions
            if listing is not None and self._listing_manager is session.state_manager
            else None
        )
        if revisions is None:
            self.revision_table.set_content(LISTING)
            self.revision_current.set_visibility(False)
            self.restore_row.set_visibility(False)
            return
        table, current, restorable = revisions
        self.revision_table.set_content(table)
        self.revision_current.set_text(f"Parameters are currently at revision {current}.")
        self.revision_current.set_visibility(current is not None)
        self.restore_row.set_visibility(bool(restorable))
        if self.revision_select.options != restorable:
            self.revision_select.set_options(restorable)
        if self.revision_select.value not in restorable:
            # A selection left over from another session's history (after a Load).
            self.revision_select.value = restorable[0] if restorable else None
        blocked = self.controller.blocked_reason()
        self.restore_button.set_enabled(blocked is None)
        self.restore_tip.set_text(RESTORE_TIP if blocked is None else f"{blocked} before restoring a revision.")

    def _listing_key(self) -> tuple:
        """What, when it changes, can change the sessions on disk outside
        this tab's own actions: a run finishing, or another active job."""
        state = self.state
        return state.job_id, state.job_done

    def _change_key(self) -> tuple:
        """What, when it changes, can change the listing or the revisions."""
        state = self.state
        return (
            self._listing_key(),
            id(state.interactive_session.state_manager),
            id(state.messages),
            len(state.messages),
        )

    # ── Reads, off the event loop ────────────────────────────────────────────

    def set_shown(self, shown: bool) -> None:
        """The layout's tab switch: opening the tab re-reads the listing."""
        self.shown = shown
        if shown:
            self.start_refresh()
            self.sync()

    def start_refresh(self, *, relist: bool = True) -> None:
        """Re-read the active session's revisions, and the sessions unless
        *relist* is False and the listing key is unchanged, off the event
        loop; a refresh started after this one wins."""
        background_tasks.create(self._refresh(*self._begin_refresh(relist)), name="sessions listing")

    def _begin_refresh(self, relist: bool) -> tuple:
        # Done at once, not when the read's task first runs: a sync before
        # then sees the key as refreshed and starts no second read.
        self._generation += 1
        self._refresh_key = self._change_key()
        listing_key = self._listing_key()
        keep = (
            self._listing
            if not relist and self._listing is not None and self._listed_key == listing_key
            else None
        )
        self._listed_key = listing_key
        session = self.state.interactive_session
        self.refreshing = True
        self.spinner.set_visibility(True)
        return self._generation, session.state_manager if session.has_active_job() else None, keep

    async def _refresh(self, generation: int, state_manager, keep: Optional[_Listing]) -> None:
        try:
            listing = await run.io_bound(_read_listing, self.controller.output_base, state_manager, keep)
        except Exception as exc:
            # The task's boundary: any failure is shown and ends the spinner
            # (one it didn't catch left it spinning, Review Stage R3). The
            # next change, Refresh or tab open reads again.
            logger.warning("reading the sessions failed", exc_info=True)
            if generation == self._generation:
                self.read_error = f"Could not read the sessions: {exc}"
                self.refreshing = False
                self._listed_key = None  # the next refresh re-lists
                self.sync()
            return
        if generation != self._generation:
            return  # stale: a later refresh is under way
        self.refreshing = False
        self.read_error = None
        drawn = self._drawn
        if drawn is not None and (listing.completed, listing.failed) == (drawn.completed, drawn.failed):
            # The same sessions: keep the rows drawn (Review Stage R3).
            self._drawn = listing
        self._listing, self._listing_manager = listing, state_manager
        self.sync()

    # ── Handlers (widgets -> state) ─────────────────────────────────────────

    def _start(self) -> None:
        self.controller.start_action()
        self.error = None

    def _relist(self) -> None:
        """Redraw the page, re-reading the listing (this tab's own action
        changed it)."""
        if self.shown:
            self.start_refresh()
        self.controller.sync()

    def _on_refresh(self) -> None:
        self._start()
        self._relist()

    def _on_select(self, job_id: str, selected: bool) -> None:
        if selected:
            self._selected.add(job_id)
        else:
            self._selected.discard(job_id)
        self._sync_rows()

    def _on_load(self, job: dict) -> None:
        self._start()
        controller = self.controller
        if controller.blocked_reason() is not None:
            controller.sync()
            return
        try:
            sessions.resume_session(self.state, job, controller.output_base)
        except (OSError, ValueError) as exc:
            self.error = f"Could not load {job['job_id']}: {exc}"
            controller.sync()
            return
        self._renaming = None
        controller.sync()
        self._on_loaded()

    def _on_rename_toggle(self, job: dict) -> None:
        self._start()
        job_id = job["job_id"]
        if self._renaming == job_id:
            self._renaming = None
        else:
            self._renaming = job_id
            self._rows[job_id].name_input.value = job.get("job_name") or ""
        self.sync()

    def _on_rename_cancel(self) -> None:
        self._renaming = None
        self.sync()

    async def _on_rename_save(self, job: dict) -> None:
        self._start()
        if self.controller.busy is not None:
            self.sync()
            return
        new_name = self._rows[job["job_id"]].name_input.value or ""
        error = await run.io_bound(sessions.rename_session_file, job, new_name)
        if error:
            self.error = error
        else:
            sessions.apply_session_rename(self.state, job, new_name)
            self._renaming = None
            self._drawn = None  # the row shows the new name now, not after the re-list
        self._relist()

    def _on_delete(self, job: dict) -> None:
        self._start()
        self._deleting = job
        self.delete_message.set_content(
            f"Delete session `{job['job_id']}` and all its files? This cannot be undone."
        )
        self.delete_dialog.open()
        self.sync()

    def _on_delete_cancel(self) -> None:
        self._deleting = None
        self.delete_dialog.close()

    async def _on_delete_confirm(self) -> None:
        job, self._deleting = self._deleting, None
        self.delete_dialog.close()
        self._start()
        controller = self.controller
        # Re-checked: a run or a save may have started since the dialog opened.
        if job is None or controller.busy is not None or job["job_id"] == run_control.active_bridge_job_id(
            self.state
        ):
            controller.sync()
            return
        controller.busy = DELETING
        controller.sync()
        try:
            error = await run.io_bound(sessions.remove_session_dir, job)
        finally:
            controller.busy = None
        if error:
            self.error = error
        else:
            sessions.forget_session(self.state, job)
            self._selected.discard(job["job_id"])
        self._relist()

    def _on_restore(self) -> None:
        self._start()
        controller = self.controller
        revision = self.revision_select.value
        if revision is None or controller.blocked_reason() is not None:
            controller.sync()
            return
        reply(self.state, sessions.undo_to_revision(self.state, int(revision)))
        controller.sync()

    async def _on_compare(self) -> None:
        self._start()
        listing = self._listing
        job_dirs = [job["job_dir"] for job in (listing.completed if listing else []) if job["job_id"] in self._selected]
        if len(job_dirs) < 2:
            self.sync()
            return
        try:
            comparison = await run.io_bound(compare_jobs, job_dirs)
        except Exception as exc:
            # As Streamlit's Compare: any failure of the comparison is shown.
            logger.warning("comparing %s failed", job_dirs, exc_info=True)
            self.error = f"Comparison failed: {exc}"
            self.sync()
            return
        self.state.comparison = comparison
        self.controller.sync()
        self._on_compared()
