"""The Results tab: the Session Viewer and Compare
Sessions.

The Session Viewer shows the viewed session (a Load), else the active one:
running, failed, or its completed results
-- the "Docked with" caption, the affinity chart, the 3Dmol viewer beside
the ligand and pose grids, then Save results as…, the Session Log and the
downloads, in that order.

Like the box picker, :meth:`ResultsView.sync` touches no file. A result is
read off the event loop (``run.io_bound``) once, when what is shown changes
(:meth:`ResultsView._load_key`): a run finishing, a redock, a Load, New
Session. While a run goes, the timer's :meth:`ResultsView.poll` re-reads
``job.log`` only if it changed.

Downloads are links to the ``/files/<job_id>/<path>`` route
(``ui_nice/app.py``), so the browser streams them and no file's bytes are
held here. Saves copy off the event loop through the controller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from nicegui import background_tasks, run, ui

from services.llm_orchestrator.interactive_session import SAVED_RESULTS_DIRNAME
from services.llm_orchestrator.orchestrator import skipped_ligand_notes
from ui.core import results_view, run_control, save_results
from ui.core.sessions import load_workflow_results
from ui.results import build_comparison_html, make_affinity_chart_figure
from ui_nice.controller import PageController
from ui_nice.mol_viewer import MolViewer
from ui_nice.widgets import markdown

logger = logging.getLogger(__name__)

TAB_VIEWER = "Session Viewer"
TAB_COMPARE = "Compare Sessions"
NO_SESSION = (
    "No session selected yet. Configure parameters on the Setup tab and click Run Docking, "
    "or Load a session from the Sessions tab."
)
RUNNING_NOTE = "Progress and Stop are on the Setup tab."
NO_RESULTS = "No results found for this session."
RECEPTOR_MISSING = "Receptor structure not found on disk."
COMPARE_HINT = "Select 2+ completed sessions in the Sessions tab and click Compare."
COMPARE_CAPTION = "Green = strongest predicted binding (most negative kcal/mol). N/A = not docked in that session."
SAVE_CAPTION = f"Copies this session's results into `{SAVED_RESULTS_DIRNAME}/<name>/` inside the session folder."
SHOWN = "●"
# The statuses a result is drawn for; anything else reads as "no results".
_RESULT_STATUSES = ("completed", "failed", "cancelled")
# NiceGUI 3.17's Plotly element resizes the chart from a ResizeObserver,
# which also fires when the browser hides the chart -- leaving the Results
# tab (its keep-alive panel is detached) or the Session Viewer -- and
# Plotly throws "Resize must be passed a displayed plot div element". The
# observer fires again once the chart is shown, so a resize of a chart not
# displayed is skipped. Re-check on a
# NiceGUI upgrade: the element may guard this itself.
PLOTLY_RESIZE_GUARD = """
<script type="module">
import { Plotly } from "nicegui-plotly";
const resize = Plotly.Plots.resize;
Plotly.Plots.resize = (gd) => (gd && gd.isConnected && gd.offsetParent !== null ? resize(gd) : Promise.resolve());
</script>
"""


@dataclass
class _Read:
    """One session as read off the event loop."""

    result: Optional[dict] = None
    data: Optional[results_view.ResultsData] = None
    log_stat: Optional[tuple[int, int]] = None
    log_text: Optional[str] = None
    error: Optional[str] = None  # the read itself failed


def _read_session(output_base: Path, job_id: str, live_result: Optional[dict], read_result: bool) -> _Read:
    """The slow part of a load, in one trip: the result (unless the live
    bridge has it), its files if it completed, and ``job.log``."""
    read = _Read(result=live_result)
    if read_result and live_result is None:
        read.result = load_workflow_results(output_base, job_id)
    if read.result and read.result.get("status") == "completed":
        read.data = results_view.read_results_data(output_base, job_id, read.result)
    read.log_stat, read.log_text = results_view.job_log_if_changed(output_base, job_id, None)
    return read


@dataclass
class _Shown:
    """What the Session Viewer draws, derived from a load."""

    job_id: Optional[str] = None
    kind: str = "none"  # none | running | error | result | missing
    result: dict = field(default_factory=dict)
    data: Optional[results_view.ResultsData] = None
    live: bool = False
    message: str = ""


class ResultsView:
    def __init__(self, controller: PageController) -> None:
        self.controller = controller
        self.state = controller.state
        self.output_base = controller.output_base
        self._shown = _Shown()
        # The load key drawn, and the one being read.
        self._loaded: Optional[tuple] = None
        self._loading: Optional[tuple] = None
        # The Session Log as last read, and what the log panel shows.
        self._log_stat: Optional[tuple[int, int]] = None
        self._log_text: Optional[str] = None
        self._drawn_log: Optional[str] = None
        # Save results as…: the key its block reason was computed for.
        self._reason: Optional[str] = None
        self._reason_key: Optional[tuple] = None
        self._reason_loading: Optional[tuple] = None
        # The pose shown per (job, ligand), 1-based; the ligand whose poses
        # the pose grid lists.
        self._poses: dict[tuple[str, str], int] = {}
        self._pose_ligand: Optional[str] = None
        self.viewer: Optional[MolViewer] = None
        self.status_line: Optional[ui.markdown] = None
        self.ligand_grid: Optional[ui.aggrid] = None
        self.pose_grid: Optional[ui.aggrid] = None
        self.pose_caption: Optional[ui.label] = None
        self._drawn_comparison: Optional[dict] = None

        ui.add_head_html(PLOTLY_RESIZE_GUARD)
        with ui.tabs().classes("w-full") as self.inner_tabs:
            ui.tab(TAB_VIEWER)
            ui.tab(TAB_COMPARE)
        with ui.tab_panels(self.inner_tabs, value=TAB_VIEWER).classes("w-full"):
            with ui.tab_panel(TAB_VIEWER):
                with ui.column().classes("w-full gap-2").mark("session-viewer") as self.viewer_column:
                    self.body = ui.column().classes("w-full gap-2").mark("results-body")
                    self._build_save_panel()
                    self._build_log_panel()
                    self.downloads = ui.column().classes("w-full gap-1").mark("results-downloads")
            with ui.tab_panel(TAB_COMPARE):
                self.compare = ui.column().classes("w-full").mark("compare")

        controller.add_view(self)
        self.sync()

    def show_compare(self) -> None:
        """Open Compare Sessions (the Sessions tab's Compare does)."""
        self.inner_tabs.set_value(TAB_COMPARE)

    def show_viewer(self) -> None:
        """Open the Session Viewer (a Load does)."""
        self.inner_tabs.set_value(TAB_VIEWER)

    # ── Building ─────────────────────────────────────────────────────────────

    def _build_save_panel(self) -> None:
        with ui.expansion("Save results as…").classes("w-full").mark("save-results") as self.save_panel:
            markdown(SAVE_CAPTION).classes("text-caption")
            self.bundle_name = ui.input("Bundle name", placeholder="e.g. trial_a").classes("w-full").mark(
                "bundle-name"
            )
            self.scope = ui.select(
                {scope: save_results.SCOPE_LABELS[scope] for scope in save_results.SAVE_SCOPES},
                value=save_results.SAVE_SCOPES[0], label="Include",
            ).classes("w-full").mark("save-scope")
            self.save_button = ui.button("Save results bundle", on_click=self._on_save).classes("w-full").mark(
                "save-bundle"
            )
            self.reason_label = markdown().classes("text-caption").mark("save-reason")
            with ui.column().classes("w-full gap-1").mark("save-conflict") as self.conflict:
                self.conflict_label = markdown().classes("text-warning")
                with ui.row():
                    self.overwrite_button = ui.button(
                        "Overwrite", on_click=lambda: self._on_conflict(overwrite=True)
                    ).props("outline").mark("save-overwrite")
                    self.new_name_button = ui.button(
                        on_click=lambda: self._on_conflict(overwrite=False)
                    ).props("outline").mark("save-new-name")
                    self.cancel_button = ui.button("Cancel", on_click=self._on_conflict_cancel).props(
                        "flat"
                    ).mark("save-cancel")

    def _build_log_panel(self) -> None:
        with ui.expansion("Session Log").classes("w-full").mark("session-log") as self.log_panel:
            self.log_code = ui.code(language=None).classes("w-full").style(
                "max-height: 60vh; overflow: auto"
            ).mark("session-log-text")
            self.log_link = ui.link("Download full log").mark("download-link")

    def _draw_body(self) -> None:
        """The viewed session's status, captions, chart, viewer and grids;
        redrawn on every load."""
        shown, job_id = self._shown, self._shown.job_id
        self.body.clear()
        self.downloads.clear()
        self.viewer = self.ligand_grid = self.pose_grid = self.pose_caption = self.status_line = None
        with self.body:
            if shown.kind == "none":
                ui.label(NO_SESSION).classes("text-grey-7").mark("results-empty")
                return
            if shown.kind == "missing":
                ui.label(NO_RESULTS).classes("text-grey-7").mark("no-results")
                return
            if shown.kind == "running":
                self.status_line = markdown(self._status_text()).mark("results-status")
                ui.label(RUNNING_NOTE).classes("text-caption text-grey-7")
                return
            if shown.kind == "error":
                markdown(shown.message).classes("text-negative").mark("results-status")
                return
            status = shown.result.get("status")
            if status in ("failed", "cancelled"):
                markdown(f"Session `{job_id}` failed.").classes("text-negative").mark("results-status")
                for err in shown.result.get("errors", []):
                    ui.label(str(err)).classes("text-caption").mark("results-error")
                return
            self.status_line = markdown(self._status_text()).mark("results-status")
            if shown.live:
                self.status_line.classes("text-positive")
            for note in skipped_ligand_notes(shown.result):
                ui.label(f"Not docked (could not be downloaded): {note}").classes(
                    "text-warning"
                ).mark("results-skipped-ligand")
            caption = results_view.docked_with_caption(shown.result)
            if caption:
                ui.label(caption).classes("text-caption").mark("docked-with")
            self._draw_chart()
            self._draw_viewer_and_grids()
        with self.downloads:
            self._draw_downloads()

    def _job_name(self) -> Optional[str]:
        shown = self._shown
        # The active session's name as it is now: state.job_name names
        # state.job_id, and a rename follows into it.
        if shown.live or shown.job_id == self.state.job_id:
            return self.state.job_name
        return shown.result.get("job_name")

    def _status_text(self) -> str:
        """The status line of a running or completed session."""
        shown = self._shown
        label = results_view.job_label(shown.job_id, self._job_name())
        if shown.kind == "running":
            return f"Session {label} is running..."
        return f"Session {label} completed" if shown.live else f"Viewing session {label}"

    def _draw_chart(self) -> None:
        dock_results = self._shown.result.get("results", {}).get("dock", {}).get("results", [])
        figure = make_affinity_chart_figure(dock_results)
        if figure is None:
            return
        with ui.scroll_area().classes("w-full").style("height: 400px").mark("affinity-chart"):
            ui.plotly(figure).classes("w-full").style(f"height: {figure.layout.height}px")

    def _draw_viewer_and_grids(self) -> None:
        data = self._shown.data
        if data is None:
            return
        if data.receptor_missing:
            ui.label(RECEPTOR_MISSING).classes("text-caption").mark("receptor-missing")
        if data.receptor_pdb is None:
            return
        dock_results = results_view.dock_results_by_ligand(self._shown.result)
        ligands = [ligand for ligand in dock_results if ligand in data.pose_records]
        if self._pose_ligand not in ligands:
            self._pose_ligand = ligands[0] if ligands else None
        with ui.splitter(value=60).classes("w-full").style("min-height: 400px") as splitter:
            with splitter.before:
                # A height of its own, as in the box picker.
                with ui.element("div").classes("w-full").style("height: 60vh; min-height: 400px"):
                    self.viewer = MolViewer(data.receptor_pdb, self._viewer_ligands(), mode="view").classes(
                        "w-full h-full"
                    ).mark("results-viewer")
            with splitter.after:
                with ui.column().classes("w-full p-2 gap-1"):
                    ui.label("Ligands").classes("font-medium")
                    self.ligand_grid = ui.aggrid(
                        _grid_options(
                            [
                                {"field": "ligand", "headerName": "Ligand"},
                                {"field": "best", "headerName": "Best (kcal/mol)", "sort": "asc"},
                                {"field": "poses", "headerName": "Poses"},
                                {"field": "shown", "headerName": "Shown pose"},
                            ],
                            self._ligand_rows(),
                        )
                    ).classes("w-full").style("height: 30vh").mark("ligand-grid")
                    self.ligand_grid.on("cellClicked", self._on_ligand_clicked)
                    self.pose_caption = ui.label().classes("font-medium").mark("pose-caption")
                    self.pose_grid = ui.aggrid(
                        _grid_options(
                            [
                                {"field": "mode", "headerName": "Mode"},
                                {"field": "affinity", "headerName": "Affinity (kcal/mol)"},
                                {"field": "shown", "headerName": "Shown"},
                            ],
                            self._pose_rows(),
                        )
                    ).classes("w-full").style("height: 25vh").mark("pose-grid")
                    self.pose_grid.on("cellClicked", self._on_pose_clicked)
                    self._sync_pose_caption()

    def _draw_downloads(self) -> None:
        shown, job_id = self._shown, self._shown.job_id
        data = shown.data
        if data is None:
            return
        if results_view.dock_results_by_ligand(shown.result):
            ui.label("Docked Poses").classes("text-lg font-medium")

            def draw_pose_links(fmt: str) -> None:
                pose_links.clear()
                with pose_links:
                    for ligand_id, path in data.pose_downloads.get(fmt, []):
                        self._download_link(f"Download {ligand_id} ({fmt})", job_id, path)

            fmt = results_view.POSE_DOWNLOAD_FORMATS[0]
            ui.select(
                results_view.POSE_DOWNLOAD_FORMATS, value=fmt, label="Download format",
                on_change=lambda e: draw_pose_links(e.value),
            ).classes("w-40").mark("pose-format")
            pose_links = ui.column().classes("w-full gap-0").style("max-height: 300px; overflow-y: auto")
            draw_pose_links(fmt)
        ui.label("Other Files").classes("text-lg font-medium")
        for path in data.other_files:
            self._download_link(f"Download {path.name}", job_id, path)

    def _download_link(self, text: str, job_id: str, path: Path) -> None:
        url = results_view.job_file_url(self.output_base, job_id, path)
        if url is None:
            return
        link = ui.link(text, url).mark("download-link")
        link.props["download"] = path.name

    # ── Rows and viewer poses (no I/O) ──────────────────────────────────────

    def _selected_pose(self, ligand: str) -> int:
        return self._poses.get((self._shown.job_id, ligand), 1)

    def _viewer_ligands(self) -> dict[str, dict[str, Any]]:
        data = self._shown.data
        dock_results = results_view.dock_results_by_ligand(self._shown.result)
        return {
            ligand: results_view.viewer_ligand(records, dock_results.get(ligand, {}), self._selected_pose(ligand))
            for ligand, records in data.pose_records.items()
        }

    def _ligand_rows(self) -> list[dict]:
        records = self._shown.data.pose_records
        rows = []
        for ligand, entry in results_view.dock_results_by_ligand(self._shown.result).items():
            count = len(records.get(ligand, []))
            rows.append({
                "ligand": ligand,
                "best": entry.get("best_affinity"),
                "poses": count,
                "shown": (
                    results_view.pose_label(entry.get("modes") or [], min(self._selected_pose(ligand), count))
                    if count else ""
                ),
            })
        return rows

    def _pose_rows(self) -> list[dict]:
        ligand = self._pose_ligand
        if ligand is None:
            return []
        modes = results_view.dock_results_by_ligand(self._shown.result).get(ligand, {}).get("modes") or []
        selected = self._selected_pose(ligand)
        count = len(self._shown.data.pose_records.get(ligand, []))
        return [
            {
                "mode": i,
                "affinity": modes[i - 1].get("affinity") if i - 1 < len(modes) else None,
                "shown": SHOWN if i == min(selected, count) else "",
            }
            for i in range(1, count + 1)
        ]

    def _sync_pose_caption(self) -> None:
        if self.pose_caption is None:
            return
        ligand = self._pose_ligand
        self.pose_caption.set_text(f"Poses of {ligand}" if ligand else "No poses to show")

    def _redraw_poses(self) -> None:
        # NOTE (known limitation): a pose or ligand click re-sends every ligand's shown pose
        # (set_ligands), a few KB each, so ~1 MB per click on a 300-ligand
        # batch. Fix if a click on a large batch is noticeably slow: a
        # MolViewer.set_ligand(ligand, entry) that sends only the ligand
        # whose pose changed.
        for grid, rows in ((self.ligand_grid, self._ligand_rows()), (self.pose_grid, self._pose_rows())):
            if grid is not None and grid.options["rowData"] != rows:
                grid.options["rowData"] = rows
                grid.update()
        self._sync_pose_caption()
        if self.viewer is not None:
            self.viewer.set_ligands(self._viewer_ligands())

    # ── Sync (state -> widgets) ──────────────────────────────────────────────

    def _load_key(self) -> tuple:
        """What the Session Viewer shows: a change starts a new read."""
        state = self.state
        job_id = state.viewed_job_id or state.job_id
        bridge = state.bridge if job_id and job_id == state.job_id else None
        return job_id, bridge, bool(bridge and bridge.is_running()), state.job_done

    def _save_key(self) -> tuple:
        """What Save results as…'s block reason depends on."""
        state = self.state
        session = state.interactive_session
        return (
            session.job_id if session.has_active_job() else None,
            session.state_manager,
            run_control.active_bridge_job_id(state),
            frozenset(session.pending_changed_params or ()),
            self._loaded,
        )

    def sync(self) -> None:
        key = self._load_key()
        if key != self._loaded and key != self._loading:
            self._start_load(key)
        if self.status_line is not None:
            self.status_line.set_content(self._status_text())
        self._sync_save_panel()
        self._sync_log()
        self._sync_compare()

    async def poll(self) -> None:
        # While a run goes: its log, if it changed.
        job_id = self._shown.job_id
        if self._shown.kind != "running" or job_id is None:
            return
        try:
            stat, text = await run.io_bound(
                results_view.job_log_if_changed, self.output_base, job_id, self._log_stat
            )
        except OSError:
            # The log shown stays as last read. Raised, it would stop the
            # tick before its redraw, every second of the run.
            logger.warning("reading the log of %s failed", job_id, exc_info=True)
            return
        if self._shown.job_id != job_id:
            return
        self._log_stat = stat
        if text is not None:
            self._log_text = text

    def _start_load(self, key: tuple) -> None:
        job_id = key[0]
        if job_id is None:
            # Nothing to read; called from sync(), which draws the rest.
            self._show(key, _Shown(), _Read(), resync=False)
            return
        self._loading = key
        background_tasks.create(self._load(key), name="results")

    async def _load(self, key: tuple) -> None:
        job_id, bridge, running, _ = key
        live_result, shown = None, None
        if bridge is not None:
            error = bridge.get_error()
            result = bridge.get_result()
            if running:
                shown = _Shown(job_id, "running", live=True)
            elif error:
                shown = _Shown(job_id, "error", live=True, message=f"Session failed: {error}")
            elif result and result.get("status") in _RESULT_STATUSES:
                live_result = result
        try:
            read = await run.io_bound(_read_session, self.output_base, job_id, live_result, shown is None)
        except Exception as exc:
            # The task's boundary: e.g. another uid's unreadable file, or a
            # malformed result. Say so, and don't retry; an uncaught one
            # would leave the viewer on its previous session for good.
            logger.warning("reading the results of %s failed", job_id, exc_info=True)
            read = _Read(error=str(exc))
        if self._loading != key:
            return  # something else is shown meanwhile
        if shown is None:
            if read.error:
                shown = _Shown(job_id, "error", message=f"Could not read the results: {read.error}")
            elif read.result and read.result.get("status") in _RESULT_STATUSES:
                shown = _Shown(job_id, "result", read.result, read.data, live=live_result is not None)
            else:
                shown = _Shown(job_id, "missing")
        self._show(key, shown, read)

    def _show(self, key: tuple, shown: _Shown, read: _Read, *, resync: bool = True) -> None:
        self._loading = None
        self._loaded = key
        self._shown = shown
        self._log_stat, self._log_text = read.log_stat, read.log_text
        self._draw_body()
        if resync:
            self.controller.sync()

    def _sync_log(self) -> None:
        text = self._log_text if self._shown.kind not in ("none", "missing") else None
        self.log_panel.set_visibility(text is not None)
        if text is None or text is self._drawn_log:
            return
        self._drawn_log = text
        self.log_code.set_content(results_view.job_log_tail(text))
        url = results_view.job_file_url(
            self.output_base, self._shown.job_id, self.output_base / self._shown.job_id / "job.log"
        )
        self.log_link.props["href"] = url
        self.log_link.props["download"] = "job.log"
        self.log_link.update()

    def _sync_save_panel(self) -> None:
        state, shown = self.state, self._shown
        session = state.interactive_session
        # Only for the active session: a session viewed but not active has
        # no state manager to read the revision and parameters from.
        visible = (
            shown.kind == "result" and shown.result.get("status") == "completed"
            and session.has_active_job() and session.job_id == shown.job_id
        )
        self.save_panel.set_visibility(visible)
        if not visible:
            return
        key = self._save_key()
        if key != self._reason_key and key != self._reason_loading:
            self._reason_loading = key
            background_tasks.create(self._refresh_reason(key), name="save block reason")
        busy = self.controller.busy is not None
        checked = key == self._reason_key
        reason = self._reason if checked else None
        self.save_button.set_enabled(checked and reason is None and not busy)
        self.reason_label.set_content(reason or "")
        self.reason_label.set_visibility(bool(reason))

        conflict = state.save_conflict
        has_conflict = bool(conflict and conflict["job_id"] == shown.job_id)
        self.conflict.set_visibility(has_conflict)
        if has_conflict:
            self.conflict_label.set_content(f"A bundle named `{conflict['name']}` already exists.")
            self.new_name_button.set_text(f"Save as {conflict['suggested']}")
        for button in (self.overwrite_button, self.new_name_button, self.cancel_button):
            button.set_enabled(not busy)

    async def _refresh_reason(self, key: tuple) -> None:
        # save_block_reason reads the manifest and workflow_results.json.
        try:
            reason = await run.io_bound(save_results.save_block_reason, self.state, self.output_base)
        except Exception as exc:
            # The task's boundary: an uncaught failure would leave the
            # button off with no reason, never checked again.
            logger.warning("checking whether the results can be saved failed", exc_info=True)
            reason = f"Could not check whether the results can be saved: {exc}"
        if self._reason_loading != key:
            return
        self._reason_loading = None
        self._reason, self._reason_key = reason, key
        self.controller.sync()

    def _sync_compare(self) -> None:
        comparison = self.state.comparison
        if comparison is self._drawn_comparison and self.compare.default_slot.children:
            return
        self._drawn_comparison = comparison
        self.compare.clear()
        with self.compare:
            if comparison:
                ui.html(build_comparison_html(comparison), sanitize=False).classes("w-full").mark(
                    "compare-table"
                )
                ui.label(COMPARE_CAPTION).classes("text-caption")
            else:
                ui.label(COMPARE_HINT).classes("text-grey-7").mark("compare-hint")

    # ── Handlers (widgets -> state) ─────────────────────────────────────────

    def _on_ligand_clicked(self, event) -> None:
        ligand = ((event.args or {}).get("data") or {}).get("ligand")
        if ligand is None or self._shown.data is None or ligand not in self._shown.data.pose_records:
            return
        self._pose_ligand = ligand
        self._redraw_poses()

    def _on_pose_clicked(self, event) -> None:
        mode = ((event.args or {}).get("data") or {}).get("mode")
        if self._pose_ligand is None or not isinstance(mode, int):
            return
        self._poses[(self._shown.job_id, self._pose_ligand)] = mode
        self._redraw_poses()

    def _on_save(self) -> None:
        controller = self.controller
        controller.start_action()
        name = (self.bundle_name.value or "").strip()
        controller.start_save(*save_results.plan_save(self.state, self.output_base, name, self.scope.value))
        controller.sync()

    def _on_conflict(self, *, overwrite: bool) -> None:
        controller = self.controller
        controller.start_action()
        controller.start_save(*save_results.plan_conflict_save(self.state, self.output_base, overwrite=overwrite))
        controller.sync()

    def _on_conflict_cancel(self) -> None:
        save_results.cancel_save_conflict(self.state)
        self.controller.sync()


def _grid_options(columns: list[dict], rows: list[dict]) -> dict:
    return {
        "columnDefs": [{**column, "sortable": True, "resizable": True} for column in columns],
        "rowData": rows,
        "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
        "autoSizeStrategy": {"type": "fitGridWidth"},
    }
