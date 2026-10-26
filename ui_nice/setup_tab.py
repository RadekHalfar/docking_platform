"""The Setup tab's form side (Phase 16, Stage 16.4): input mode and inputs,
the Docking Box fields, the "Current parameters" summary, Advanced, the
session name, the action buttons, the running session's progress, and the
redock confirmation dialog.

The widgets and ``state.form`` are kept in step explicitly: each widget's
change handler writes its form key, and :meth:`SetupView.sync` writes the
form back into any widget whose value differs (after a chat command, a
reset, a geometry command's deferred apply). Writing an equal value is a
no-op, so a sync never fights the user's typing. NiceGUI fires the change
handlers on those writes too; they return at once while
``controller.syncing``, so a sync never starts another.

The running session's progress is read off the event loop by
:meth:`SetupView.poll`, on timer ticks only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Optional, Sequence

from nicegui import run, ui

from common.docking_algorithm import DOCKING_ALGORITHMS, docking_algorithm_label
from services.llm_orchestrator.rerun_planner import describe_rerun_plan
from ui.core import form, results_view, run_control, uploads
from ui.job_history import JOB_NAME_MAX_CHARS
from ui.worker_cap import describe_worker_cap
from ui_nice.controller import PageController
from ui_nice.widgets import markdown

INPUT_MODES = {"rcsb": "RCSB IDs", "local_files": "Local Files", "folder": "Batch Processing"}
LIGAND_UPLOAD_LABEL = (
    "Ligand file(s) (.sdf, .mol, .mol2, .pdbqt). Each drop adds to the list; "
    "Clear empties it. A multi-molecule .sdf is split into one ligand per molecule."
)
LIGAND_ACCEPT = ".sdf,.mol,.mol2,.pdbqt"
VINA_CPU_CAPTION = (
    "Vina's search is multithreaded: more CPUs per ligand or a lower "
    "exhaustiveness make each ligand finish sooner. Workers × CPUs per "
    "ligand never exceeds the docking service's CPU limit."
)
PSO_CPU_CAPTION = "Moldina (PSO) uses 1 CPU per ligand and ignores Exhaustiveness."
STOP_REQUESTED = "Stop requested — stopping (docking halts within a few seconds)..."

_CENTER_FIELDS = (("param_center_x", "Center X"), ("param_center_y", "Center Y"), ("param_center_z", "Center Z"))
_BOX_FIELDS = (("param_box_x", "Box X"), ("param_box_y", "Box Y"), ("param_box_z", "Box Z"))


def _save_uploads(files: Sequence[tuple[str, bytes]], upload_dir: Path) -> list[Path]:
    """One drop's files, saved in one trip off the event loop."""
    return [uploads.save_upload(name, data, upload_dir) for name, data in files]


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SetupView:
    def __init__(self, controller: PageController) -> None:
        self.controller = controller
        self.state = controller.state
        # form key -> the widgets showing it (Protein ID appears in two input modes)
        self._fields: dict[str, list] = {}
        # (job_id, its docking_progress.json) as the last poll read it.
        self._progress: tuple[Optional[str], Optional[dict]] = (None, None)
        self._docking_labels: list[ui.label] = []

        with ui.column().classes("w-full p-2 gap-2"):
            ui.label("Input & Parameters").classes("text-lg font-medium")
            with ui.expansion("Current parameters", value=True).classes("w-full") as self.summary_box:
                self.summary = markdown().mark("params-summary")

            self._radio("input_mode", INPUT_MODES, self._on_input_mode)
            ui.separator()
            self._build_inputs()
            ui.separator()
            self._build_docking_box()
            self._build_advanced()
            ui.separator()
            self._text("param_job_name", "Session name (optional)").props(f"maxlength={JOB_NAME_MAX_CHARS}").tooltip(
                "A label to help you find this session later in the Sessions tab."
            )
            ui.separator()
            self._build_actions()
            self.error_label = ui.label().classes("text-negative").mark("action-error")
            self._build_run_card()
        self._build_redock_dialog()

        controller.add_view(self)
        self.sync()

    # ── Building ─────────────────────────────────────────────────────────────

    def _register(self, key: str, element):
        self._fields.setdefault(key, []).append(element)
        return element.mark(key)

    def _text(self, key: str, label: str, *, placeholder: str | None = None,
              commit: Callable[[Any], None] | None = None) -> ui.input:
        element = ui.input(
            label, placeholder=placeholder, value=self.state.form.get(key, ""),
            on_change=lambda e, k=key: self._on_text(k, e.value),
        )
        if commit is not None:
            # Committed on Enter / leaving the field: not one chat reply
            # per keystroke.
            # (No parameters: NiceGUI would pass the event to one.)
            def on_commit() -> None:
                self._on_commit(commit)

            element.on("blur", on_commit)
            element.on("keydown.enter", on_commit)
        return self._register(key, element)

    def _radio(self, key: str, options: dict, on_change: Callable) -> ui.radio:
        element = ui.radio(options, value=self.state.form.get(key), on_change=on_change).props("inline")
        return self._register(key, element)

    def _build_inputs(self) -> None:
        with ui.column().classes("w-full") as self.rcsb_panel:
            self._text("param_protein_id", "Protein ID (PDB)", placeholder="e.g. 6lu7",
                       commit=form.commit_protein_id).classes("w-full")
            self._text("param_ligand_ids_text", "Ligand IDs (comma-separated)",
                       placeholder="e.g. ATP, HEM, ADP", commit=form.commit_ligand_ids).classes("w-full")

        with ui.column().classes("w-full") as self.local_panel:
            ui.upload(
                label="Protein file (.pdb, .cif)", auto_upload=True, on_upload=self._on_protein_upload,
            ).props('accept=".pdb,.cif"').classes("w-full").mark("upload-protein")
            with ui.row().classes("items-center") as self.protein_row:
                self.protein_caption = ui.label().classes("text-caption")
                ui.button("Clear", on_click=self._on_clear_protein).props("flat dense size=sm")
            self._ligand_upload("upload-ligands", folder_mode=False)
            self.local_ligand_row = self._ligand_caption_row()

        with ui.column().classes("w-full") as self.folder_panel:
            self._text("param_protein_id", "Protein ID (PDB)", placeholder="e.g. 6lu7",
                       commit=form.commit_protein_id).classes("w-full")
            self._ligand_upload("upload-folder-ligands", folder_mode=True)
            self.folder_ligand_row = self._ligand_caption_row()

    def _ligand_upload(self, marker: str, *, folder_mode: bool) -> ui.upload:
        return ui.upload(
            label=LIGAND_UPLOAD_LABEL, multiple=True, auto_upload=True,
            on_multi_upload=lambda e: self._on_ligand_upload(e, folder_mode=folder_mode),
        ).props(f'accept="{LIGAND_ACCEPT}"').classes("w-full").mark(marker)

    def _ligand_caption_row(self) -> tuple[ui.row, ui.label]:
        with ui.row().classes("items-center") as row:
            caption = ui.label().classes("text-caption")
            ui.button("Clear", on_click=self._on_clear_ligands).props("flat dense size=sm")
        return row, caption

    def _build_docking_box(self) -> None:
        ui.label("Docking Box").classes("font-medium")
        for fields, placeholder in ((_CENTER_FIELDS, "0.0"), (_BOX_FIELDS, "20.0")):
            with ui.row().classes("w-full no-wrap"):
                for key, label in fields:
                    self._text(key, label, placeholder=placeholder).classes("grow")

    def _build_advanced(self) -> None:
        with ui.expansion("Advanced").classes("w-full").mark("advanced"):
            self._radio(
                "param_docking_algorithm",
                {a: docking_algorithm_label(a) for a in DOCKING_ALGORITHMS},
                self._on_algorithm,
            )
            with ui.row().classes("w-full no-wrap"):
                self._text("param_ph", "pH").classes("grow")
                self._text("param_exhaustiveness", "Exhaustiveness").classes("grow").tooltip(
                    "Vina (Monte Carlo) only — Moldina (PSO) ignores it."
                )
            # A number input (Phase 15 Future Work): only whole numbers >= 1.
            self.cpu = ui.number(
                "CPUs per ligand", min=1, step=1, precision=0, on_change=self._on_cpu,
            ).mark("param_cpu")
            self.vina_caption = ui.label(VINA_CPU_CAPTION).classes("text-caption text-grey-7")
            self.pso_caption = ui.label(PSO_CPU_CAPTION).classes("text-caption text-grey-7")
            self.parallel = ui.checkbox(
                "Parallel ligand docking", value=bool(self.state.form.get("param_parallel")),
                on_change=self._on_parallel,
            )
            self._register("param_parallel", self.parallel)
            self.workers = ui.number(
                "Max parallel workers", min=1, step=1, precision=0, on_change=self._on_workers,
            ).mark("param_max_workers")
            self.cap_warning = ui.label().classes("text-warning").mark("worker-cap-warning")
            self.cap_caption = ui.label().classes("text-caption text-grey-7").mark("worker-cap-caption")

    def _build_actions(self) -> None:
        with ui.row().classes("w-full no-wrap"):
            self.run_button = ui.button("Run Docking", on_click=self._on_run).classes("grow").mark("run-docking")
            with self.run_button:
                self.run_tip = ui.tooltip()
            self.save_button = (
                ui.button("Save Session", on_click=self._on_save_session)
                .props("outline").classes("grow").tooltip("Save current parameters without docking.")
                .mark("save-session")
            )
            self.new_button = ui.button("New Session", on_click=self._on_new_session).props("outline").classes(
                "grow"
            ).mark("new-session")
            with self.new_button:
                self.new_tip = ui.tooltip()
            self.reset_button = ui.button("Reset", on_click=self._on_reset).props("outline").classes(
                "grow"
            ).mark("reset")
            with self.reset_button:
                self.reset_tip = ui.tooltip()

    def _build_run_card(self) -> None:
        with ui.card().classes("w-full").mark("run-status") as self.run_card:
            self.run_label = markdown()
            self.progress = ui.linear_progress(value=0, show_value=False)
            self.progress_label = ui.label().classes("text-caption")
            self.docking_now = ui.column().classes("gap-0")
            self.stop_button = ui.button("Stop", on_click=self._on_stop).props("color=negative").mark("stop")
            self.stopping_label = ui.label(STOP_REQUESTED).classes("text-caption")

    def _build_redock_dialog(self) -> None:
        # Persistent: only Confirm or Cancel close it, as only they resolve
        # the staged plan.
        with ui.dialog().props("persistent").mark("redock-dialog") as self.redock_dialog, ui.card():
            ui.label("Confirm redock").classes("text-lg font-medium")
            self.redock_plan = ui.label().style("white-space: pre-line").mark("redock-plan")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._on_cancel_redock).props("flat").mark("cancel-redock")
                ui.button("Confirm redock", on_click=self._on_confirm_redock).mark("confirm-redock")

    # ── Sync (state -> widgets) ──────────────────────────────────────────────

    def sync(self) -> None:
        form_values = self.state.form
        for key, elements in self._fields.items():
            value = form_values.get(key)
            for element in elements:
                if element.value != value:
                    element.value = value
        self._sync_inputs()
        self._sync_advanced()
        self._sync_summary()
        self._sync_actions()
        self._sync_run_card()
        self._sync_redock_dialog()

    async def poll(self) -> None:
        job_id = run_control.active_bridge_job_id(self.state)
        progress = (
            await run.io_bound(results_view.read_docking_progress, self.controller.output_base, job_id)
            if job_id else None
        )
        self._progress = (job_id, progress)

    def _sync_inputs(self) -> None:
        mode = self.state.form.get("input_mode")
        self.rcsb_panel.set_visibility(mode == "rcsb")
        self.local_panel.set_visibility(mode == "local_files")
        self.folder_panel.set_visibility(mode == "folder")

        params = self.state.params
        protein_file = params.get("protein_file")
        self.protein_row.set_visibility(bool(protein_file))
        if protein_file:
            self.protein_caption.set_text(f"Protein: {PurePath(protein_file).name}")
        ligand_files = params.get("ligand_files") or []
        for row, caption in (self.local_ligand_row, self.folder_ligand_row):
            row.set_visibility(bool(ligand_files))
            caption.set_text(f"{len(ligand_files)} ligand file(s) ready")

    def _sync_advanced(self) -> None:
        state = self.state
        algorithm = form.selected_docking_algorithm(state)
        vina = algorithm == "vina"
        self.cpu.set_visibility(vina)
        self.vina_caption.set_visibility(vina)
        self.pso_caption.set_visibility(not vina)
        cpu = _as_int(state.form.get("param_cpu"))
        if self.cpu.value != cpu:
            self.cpu.value = cpu

        # The bound first: the server clamps workers to its CPU quota //
        # CPUs per ligand, and so does the field (form.max_workers).
        self.workers.max = form.max_workers(state)
        workers = state.form.get("param_max_workers")
        if self.workers.value != workers:
            self.workers.value = workers
        self.workers.set_enabled(bool(state.form.get("param_parallel")))

        warning, caption = describe_worker_cap(state.worker_cap, algorithm)
        self.cap_warning.set_text(warning or "")
        self.cap_warning.set_visibility(bool(warning))
        self.cap_caption.set_text(caption)

    def _sync_summary(self) -> None:
        summary = form.params_summary(self.state)
        self.summary_box.set_visibility(summary is not None)
        if summary is not None:
            self.summary.set_content(summary)

    def _sync_actions(self) -> None:
        state = self.state
        has_job = state.interactive_session.has_active_job()
        blocked = self._blocked()
        idle = blocked is None

        # Run Docking is off while this client's run is
        # going: a redock confirmed then would replace the running bridge
        # and orphan its job. The same while a save copies the results.
        self.run_button.set_enabled(idle)
        self.run_tip.set_text(
            "Start docking, or redock the active session with the changed parameters."
            if idle else f"{blocked} before docking again."
        )
        self.save_button.set_enabled(has_job)
        self.new_button.set_enabled(has_job and idle)
        self.new_tip.set_text(
            "Keep your parameters, start a fresh session."
            if idle else f"{blocked} before starting a new one."
        )
        self.reset_button.set_enabled(idle)
        self.reset_tip.set_text(
            "Clear everything, including entered parameters."
            if idle else f"{blocked} before resetting."
        )
        error = self.controller.error
        self.error_label.set_text(error or "")
        self.error_label.set_visibility(bool(error))

    def _blocked(self) -> Optional[str]:
        """Why Run, New Session and Reset are off, or None."""
        return self.controller.blocked_reason()

    def _sync_run_card(self) -> None:
        state = self.state
        job_id = run_control.active_bridge_job_id(state)
        self.run_card.set_visibility(job_id is not None)
        if job_id is None:
            return
        self.run_label.set_content(f"Session {results_view.job_label(job_id, state.job_name)} is running...")

        polled_job, progress = self._progress
        if polled_job != job_id:
            progress = None  # a new run, not polled yet
        running = bool(progress) and progress.get("status") == "running"
        total = (progress or {}).get("total") or 0
        completed = (progress or {}).get("completed") or 0
        self.progress.set_visibility(running and bool(total))
        self.progress_label.set_visibility(running and bool(total))
        if running and total:
            self.progress.set_value(completed / total)
            self.progress_label.set_text(f"{completed} / {total} ligands docked")
        texts = [
            f"Currently docking: {ligand}" + (f" (running {elapsed}s)" if elapsed is not None else "")
            for ligand, elapsed in (
                results_view.currently_docking(progress, datetime.now(timezone.utc)) if running else ()
            )
        ]
        # The labels are kept and retexted; only a changed count adds or
        # removes one (the elapsed time changes every tick).
        labels = self._docking_labels
        while len(labels) < len(texts):
            with self.docking_now:
                labels.append(ui.label().classes("text-caption"))
        while len(labels) > len(texts):
            labels.pop().delete()
        for label, text in zip(labels, texts):
            label.set_text(text)

        stop_requested = state.bridge.stop_requested()
        self.stop_button.set_visibility(not stop_requested)
        self.stopping_label.set_visibility(stop_requested)

    def _sync_redock_dialog(self) -> None:
        pending = self.state.pending_rerun
        if pending:
            self.redock_plan.set_text(describe_rerun_plan(pending["steps"]))
            if not self.redock_dialog.value:
                self.redock_dialog.open()
        elif self.redock_dialog.value:
            self.redock_dialog.close()

    # ── Handlers (widgets -> state) ─────────────────────────────────────────

    def _on_text(self, key: str, value: Optional[str]) -> None:
        if self.controller.syncing:
            return
        self.state.form[key] = value or ""
        if key in form.BOX_FORM_KEYS:
            # The box picker draws these fields: move its box too.
            self.controller.sync()
        else:
            self._sync_summary()

    def _on_commit(self, commit: Callable[[Any], None]) -> None:
        commit(self.state)
        self.controller.sync()

    def _on_input_mode(self, event) -> None:
        if self.controller.syncing:
            return
        if event.value:
            self.state.form["input_mode"] = event.value
        self._sync_inputs()

    def _on_algorithm(self, event) -> None:
        if self.controller.syncing:
            return
        if event.value:
            form.set_docking_algorithm(self.state, event.value)
        self.controller.sync()

    def _on_cpu(self, event) -> None:
        # A cleared field is not written: the user is typing a new number.
        if self.controller.syncing or event.value is None:
            return
        self.state.form["param_cpu"] = max(1, int(event.value))
        self._sync_advanced()
        self._sync_summary()

    def _on_parallel(self, event) -> None:
        if self.controller.syncing:
            return
        self.state.form["param_parallel"] = bool(event.value)
        self.workers.set_enabled(bool(event.value))

    def _on_workers(self, event) -> None:
        if self.controller.syncing or event.value is None:
            return
        # Above the bound while typing; leaving the field clamps the widget.
        self.state.form["param_max_workers"] = max(1, min(int(event.value), int(self.workers.max)))

    async def _save_files(self, files, failure: str) -> Optional[list[Path]]:
        """Save one drop's files, or set the action error and return None."""
        controller = self.controller
        controller.start_action()
        try:
            named = [(file.name, await file.read()) for file in files]
            return await run.io_bound(_save_uploads, named, controller.upload_dir)
        except (OSError, ValueError) as exc:
            controller.error = f"{failure}: {exc}"
            return None

    async def _on_protein_upload(self, event) -> None:
        name = event.file.name
        paths = await self._save_files([event.file], f"Could not save {name}")
        if paths:
            uploads.apply_protein_upload(self.state, name, paths[0])
        event.sender.reset()
        self.controller.sync()

    async def _on_ligand_upload(self, event, *, folder_mode: bool) -> None:
        paths = await self._save_files(event.files, "Could not save the ligand files")
        if paths:
            uploads.apply_ligand_uploads(self.state, paths, folder_mode=folder_mode)
        event.sender.reset()
        self.controller.sync()

    def _on_clear_protein(self) -> None:
        uploads.clear_protein_upload(self.state)
        self.controller.sync()

    def _on_clear_ligands(self) -> None:
        uploads.clear_ligand_uploads(self.state)
        self.controller.sync()

    def _on_run(self) -> None:
        controller = self.controller
        controller.start_action()
        if self._blocked() is not None:
            controller.sync()
            return
        controller.show_outcome(run_control.run_docking(self.state, controller.output_base, controller.bridge_factory))

    def _on_save_session(self) -> None:
        self.controller.start_action()
        self.controller.show_outcome(run_control.save_session(self.state))

    def _on_new_session(self) -> None:
        self.controller.start_action()
        if self._blocked() is None:
            form.start_new_session(self.state)
        self.controller.sync()

    def _on_reset(self) -> None:
        self.controller.start_action()
        if self._blocked() is None:
            form.reset_session(self.state)
        self.controller.sync()

    def _on_stop(self) -> None:
        self.controller.show_outcome(run_control.stop_run(self.state))

    def _on_confirm_redock(self) -> None:
        controller = self.controller
        controller.start_action()
        controller.show_outcome(
            run_control.confirm_redock(self.state, controller.output_base, controller.bridge_factory)
        )

    def _on_cancel_redock(self) -> None:
        self.controller.start_action()
        self.controller.show_outcome(run_control.cancel_redock(self.state))
