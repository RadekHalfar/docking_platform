"""The NiceGUI page layout (Phase 16).

One page: a header (title, chat toggle, settings), a collapsible chat drawer
on the right, and the main-area tabs Setup / Results / Sessions. Setup holds
a resizable splitter, form on the left and viewer on the right.

Stage 16.3 built the shell; Stage 16.4 fills the chat drawer
(:mod:`ui_nice.chat_drawer`) and Setup's form side (:mod:`ui_nice.setup_tab`),
and Stage 16.5 Setup's viewer side, the box picker
(:mod:`ui_nice.box_picker_panel`). All are driven by one
:class:`~ui_nice.controller.PageController` per client.
The regions still to come say which stage fills them, and
:class:`PageParts` hands those stages what they need.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nicegui import ui

from ui.core.state import GuiSession
from ui_nice import controller as controller_module
from ui_nice.box_picker_panel import BoxPickerView
from ui_nice.chat_drawer import ChatView
from ui_nice.controller import PageController
from ui_nice.setup_tab import SetupView

PAGE_TITLE = "Molecular Docking Platform"
TAB_SETUP = "Setup"
TAB_RESULTS = "Results"
TAB_SESSIONS = "Sessions"


@dataclass
class PageParts:
    """The page's controller, views and containers, for the stages that fill them."""

    controller: PageController
    chat_drawer: ui.right_drawer
    chat: ChatView
    settings_dialog: ui.dialog
    tabs: ui.tabs
    tab_panels: ui.tab_panels
    setup_splitter: ui.splitter
    setup: SetupView
    box_picker: BoxPickerView


def build_page(state: GuiSession, output_base: Path) -> PageParts:
    """Draw the whole page for one client. ``state`` is that client's own."""
    controller = PageController(state, output_base)
    # A job that finished before this page was built is handled before the
    # first draw, as Streamlit handles it at the top of the script.
    controller.tick()
    settings_dialog = _settings_dialog()
    chat_drawer, chat = _chat_drawer(controller)
    _header(chat_drawer, settings_dialog)
    tabs, tab_panels, setup_splitter, setup, box_picker = _main_tabs(controller)
    ui.timer(controller_module.POLL_INTERVAL_S, controller.tick)
    return PageParts(
        controller, chat_drawer, chat, settings_dialog, tabs, tab_panels, setup_splitter, setup, box_picker
    )


def _header(chat_drawer: ui.right_drawer, settings_dialog: ui.dialog) -> None:
    with ui.header().classes("items-center"):
        ui.label(f"🧬 {PAGE_TITLE}").classes("text-lg font-medium")
        ui.space()
        ui.button(icon="settings", on_click=settings_dialog.open).props(
            "flat round color=white aria-label=Settings"
        ).tooltip("Settings").mark("settings-button")
        ui.button(icon="chat", on_click=chat_drawer.toggle).props(
            "flat round color=white aria-label=Chat"
        ).tooltip("Show or hide the chat").mark("chat-toggle")


def _settings_dialog() -> ui.dialog:
    with ui.dialog().mark("settings-dialog") as dialog, ui.card().classes("min-w-80"):
        ui.label("LLM settings").classes("text-lg font-medium")
        ui.label("Provider and model settings arrive in Stage 16.7.").classes("text-grey-7")
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")
    return dialog


def _chat_drawer(controller: PageController) -> tuple[ui.right_drawer, ChatView]:
    with ui.right_drawer(value=True).props("bordered width=420") as drawer:
        chat = ChatView(controller)
    return drawer, chat


def _main_tabs(
    controller: PageController,
) -> tuple[ui.tabs, ui.tab_panels, ui.splitter, SetupView, BoxPickerView]:
    with ui.tabs().classes("w-full") as tabs:
        ui.tab(TAB_SETUP)
        ui.tab(TAB_RESULTS)
        ui.tab(TAB_SESSIONS)
    with ui.tab_panels(tabs, value=TAB_SETUP).classes("w-full") as panels:
        with ui.tab_panel(TAB_SETUP):
            with ui.splitter(value=45).classes("w-full").style("min-height: 70vh") as splitter:
                with splitter.before:
                    setup = SetupView(controller)
                with splitter.after:
                    box_picker = BoxPickerView(controller)
        with ui.tab_panel(TAB_RESULTS):
            ui.label("Results arrive in Stage 16.6.")
        with ui.tab_panel(TAB_SESSIONS):
            ui.label("Session history arrives in Stage 16.7.")
    return tabs, panels, splitter, setup, box_picker
