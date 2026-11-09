"""The Setup tab's viewer side: the visual box picker.

"Load receptor for box picking" starts a background prepare job; once a
receptor is prepared it is shown in a :class:`~ui_nice.mol_viewer.MolViewer`
with the docking box drawn on it. A click on a receptor atom sets the box
center, and the − / + buttons step the box size. Both write the Docking Box
fields on the form side, which stay the only box state
(:mod:`ui.core.box_picker`). Every :meth:`BoxPickerView.sync` draws those
fields, so typing in them, a chat command or a geometry command moves the
box as well.

The receptor file is read off the event loop (``run.io_bound``), once per
receptor; :meth:`BoxPickerView.sync` itself touches no file. A receptor
that can't be read (gone from disk, or unreadable) is shown as such, with a
Reload button, and not read again until a new one is loaded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import background_tasks, run, ui

from common.path_utils import from_container_path
from ui.core import box_picker
from ui.core.form import clear_box_picker_state, read_box_fields
from ui_nice.controller import PageController
from ui_nice.mol_viewer import MolViewer

PREPARING = "Preparing receptor for box picking..."
RECEPTOR_GONE = "Prepared receptor no longer on disk — reload it."
RECEPTOR_UNREADABLE = "Could not read the prepared receptor ({error}) — reload it."
NO_CENTER = "Click a receptor atom in the viewer to set the box center."
PLACEHOLDER = (
    f"Box size is not set — the viewer shows a {box_picker.PICKER_DEFAULT_BOX_SIZE:g} Å "
    "placeholder, which a click or − / + fills in."
)


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


class BoxPickerView:
    def __init__(self, controller: PageController) -> None:
        self.controller = controller
        self.state = controller.state
        self.viewer: Optional[MolViewer] = None
        # The receptor (as stored in state) the viewer shows, and the one
        # being read for it.
        self._shown: Optional[str] = None
        self._loading: Optional[str] = None
        # A receptor whose read failed, and why: (receptor, message).
        self._failed: Optional[tuple[str, str]] = None

        with ui.column().classes("w-full h-full p-2 gap-2"):
            ui.label("Pick Docking Box Visually").classes("text-lg font-medium")
            with ui.row().classes("items-center") as self.preparing:
                ui.spinner(size="sm")
                ui.label(PREPARING)
            self.error_label = ui.label().classes("text-negative").mark("box-picker-error")
            self.message_label = ui.label().mark("box-picker-message")
            self.load_button = ui.button("Load receptor for box picking", on_click=self._on_load).mark(
                "load-receptor"
            )
            with ui.column().classes("w-full gap-1") as self.gone:
                self.gone_label = ui.label(RECEPTOR_GONE).classes("text-warning").mark("receptor-gone")
                ui.button("Reload receptor", on_click=self._on_load).mark("reload-receptor")
            with ui.column().classes("w-full gap-1") as self.picker:
                self._build_steppers()
                # A height of its own: the viewer fills its parent, and a
                # parent with only a min-height gives it none.
                self.viewer_box = ui.element("div").classes("w-full").style("height: 55vh; min-height: 400px")
                self.caption = ui.label().classes("text-caption").mark("box-picker-caption")
                self.placeholder = ui.label(PLACEHOLDER).classes("text-caption").mark("box-picker-placeholder")
                ui.button("Discard picked receptor", on_click=self._on_discard).props("outline").mark(
                    "discard-receptor"
                )

        controller.add_view(self)
        self.sync()

    def _build_steppers(self) -> None:
        with ui.row().classes("w-full no-wrap gap-1"):
            for axis, name in enumerate("XYZ"):
                for delta, sign, verb, suffix in ((-1.0, "−", "Shrink", "down"), (1.0, "+", "Grow", "up")):
                    ui.button(
                        f"{name} {sign}", on_click=lambda a=axis, d=delta: self._on_step(a, d),
                    ).props("outline dense").classes("grow").tooltip(f"{verb} box {name} by 1 Å").mark(
                        f"box-step-{name.lower()}-{suffix}"
                    )

    # ── Sync (state -> widgets) ──────────────────────────────────────────────

    def sync(self) -> None:
        state = self.state
        bridge = state.box_picker_bridge
        preparing = bool(bridge and bridge.is_running())
        receptor = None if preparing else state.box_picker_receptor_pdb
        failure = self._failed[1] if self._failed and self._failed[0] == receptor else None
        gone = failure is not None
        loaded = receptor is not None and not gone

        self.preparing.set_visibility(preparing)
        error = None if preparing else state.box_picker_error
        self.error_label.set_text(error or "")
        self.error_label.set_visibility(bool(error))
        message, severity = self.controller.picker_message or ("", "")
        self.message_label.set_text(message)
        self.message_label.classes(replace="text-negative" if severity == "error" else "text-warning")
        self.message_label.set_visibility(bool(message) and not preparing)
        self.load_button.set_visibility(not preparing and receptor is None)
        self.gone.set_visibility(gone)
        if gone:
            self.gone_label.set_text(failure)
        self.picker.set_visibility(loaded)

        if not loaded:
            self._drop_viewer()
            return
        if receptor != self._shown and receptor != self._loading:
            self._loading = receptor
            path = from_container_path(receptor, self.controller.output_base)
            background_tasks.create(self._load(receptor, path), name="box picker receptor")
        self._sync_box()

    async def poll(self) -> None:
        pass  # the receptor is read once, by _load

    def _sync_box(self) -> None:
        center, _ = read_box_fields(self.state)
        if center:
            self.caption.set_text(f"Box center: ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})")
        else:
            self.caption.set_text(NO_CENTER)
        self.placeholder.set_visibility(box_picker.drawable_box_size(self.state) is None)
        if self.viewer is not None:
            # The placeholder is passed explicitly, so it is the same
            # constant a click or − / + writes into the fields.
            self.viewer.set_box(center, box_picker.viewer_box_size(self.state))

    async def _load(self, receptor: str, path: Path) -> None:
        try:
            pdb_text = await run.io_bound(_read_text, path)
        except FileNotFoundError:
            pdb_text, self._failed = None, (receptor, RECEPTOR_GONE)
        except OSError as exc:
            # e.g. another uid's 0o600 file. Recorded, so sync() doesn't
            # start the same failing read again.
            pdb_text, self._failed = None, (receptor, RECEPTOR_UNREADABLE.format(error=exc))
        if self._loading != receptor:
            return  # another receptor was loaded meanwhile
        self._loading = None
        if pdb_text is None or self.state.box_picker_receptor_pdb != receptor:
            self.controller.sync()  # unreadable, or discarded meanwhile
            return
        self._drop_viewer()
        center, _ = read_box_fields(self.state)
        with self.viewer_box:
            self.viewer = MolViewer(
                pdb_text, mode="box_picker", center=center, box_size=box_picker.viewer_box_size(self.state),
            ).classes(
                "w-full h-full"
            ).mark("box-picker-viewer")
        self.viewer.on("atom_click", self._on_atom_click)
        self._shown = receptor
        self.controller.sync()

    def _drop_viewer(self) -> None:
        if self.viewer is not None:
            self.viewer_box.clear()
            self.viewer = None
        self._shown = None

    # ── Handlers (widgets -> state) ─────────────────────────────────────────

    def _on_load(self) -> None:
        self.controller.start_action()
        started, message, severity = box_picker.start_box_picker_prep(
            self.state, self.controller.output_base, self.controller.bridge_factory
        )
        if not started:
            self.controller.picker_message = (message, severity)
        self.controller.sync()

    def _on_discard(self) -> None:
        self.controller.start_action()
        clear_box_picker_state(self.state)
        self.controller.sync()

    def _on_step(self, axis: int, delta: float) -> None:
        box_picker.step_box_size(self.state, axis, delta)
        self.controller.sync()

    def _on_atom_click(self, event) -> None:
        center = (event.args or {}).get("center")
        if not center or len(center) != 3:
            return
        box_picker.apply_viewer_click(self.state, center)
        self.controller.sync()
