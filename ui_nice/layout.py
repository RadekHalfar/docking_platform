"""The NiceGUI page layout.

One page: a header (title, chat toggle, settings), a collapsible chat drawer
on the right, and the main-area tabs Setup / Results / Sessions. Setup holds
a resizable splitter, form on the left and viewer on the right.

The parts live in their own modules: the chat drawer
(:mod:`ui_nice.chat_drawer`), Setup's form side (:mod:`ui_nice.setup_tab`),
Setup's viewer side, the box picker
(:mod:`ui_nice.box_picker_panel`), the Results tab
(:mod:`ui_nice.results_tab`), which the page switches to when a run
finishes or a session is loaded, the Sessions tab
(:mod:`ui_nice.sessions_tab`) and the settings dialog
(:mod:`ui_nice.settings_dialog`). All are driven by one
:class:`~ui_nice.controller.PageController` per client, and
:class:`PageParts` hands them to the tests.
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
from ui_nice.results_tab import ResultsView
from ui_nice.sessions_tab import SessionsView
from ui_nice.settings_dialog import SettingsView
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
    settings: SettingsView
    tabs: ui.tabs
    tab_panels: ui.tab_panels
    setup_splitter: ui.splitter
    setup: SetupView
    box_picker: BoxPickerView
    results: ResultsView
    sessions: SessionsView


def build_page(state: GuiSession, output_base: Path) -> PageParts:
    """Draw the whole page for one client. ``state`` is that client's own."""
    controller = PageController(state, output_base)
    # A job that finished before this page was built is handled before the
    # first draw.
    controller.process()
    settings = SettingsView(controller)
    chat_drawer, chat = _chat_drawer(controller, settings.open)
    _header(chat_drawer, settings)
    tabs, tab_panels, setup_splitter, setup, box_picker, results, sessions = _main_tabs(controller)
    controller.show_results = lambda: tabs.set_value(TAB_RESULTS)
    ui.timer(controller_module.POLL_INTERVAL_S, controller.tick)
    return PageParts(
        controller, chat_drawer, chat, settings, tabs, tab_panels, setup_splitter, setup, box_picker,
        results, sessions,
    )


def _header(chat_drawer: ui.right_drawer, settings: SettingsView) -> None:
    with ui.header().classes("items-center"):
        ui.label(f"🧬 {PAGE_TITLE}").classes("text-lg font-medium")
        ui.space()
        ui.button(icon="settings", on_click=settings.open).props(
            "flat round color=white aria-label=Settings"
        ).tooltip("Settings").mark("settings-button")
        ui.button(icon="chat", on_click=chat_drawer.toggle).props(
            "flat round color=white aria-label=Chat"
        ).tooltip("Show or hide the chat").mark("chat-toggle")


def _chat_drawer(controller: PageController, open_settings) -> tuple[ui.right_drawer, ChatView]:
    with ui.right_drawer(value=True).props("bordered width=420") as drawer:
        chat = ChatView(controller, open_settings)
    return drawer, chat


def _main_tabs(
    controller: PageController,
) -> tuple[ui.tabs, ui.tab_panels, ui.splitter, SetupView, BoxPickerView, ResultsView, SessionsView]:
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
            results = ResultsView(controller)
        with ui.tab_panel(TAB_SESSIONS):
            sessions = SessionsView(
                controller,
                on_load=lambda: (results.show_viewer(), tabs.set_value(TAB_RESULTS)),
                on_compare=lambda: (results.show_compare(), tabs.set_value(TAB_RESULTS)),
            )
    # Opening the Sessions tab re-reads the sessions on disk.
    tabs.on_value_change(lambda e: sessions.set_shown(e.value == TAB_SESSIONS))
    return tabs, panels, splitter, setup, box_picker, results, sessions
