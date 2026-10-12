"""The NiceGUI page layout (Phase 16, Stage 16.3).

One page: a header (title, chat toggle, settings), a collapsible chat drawer
on the right, and the main-area tabs Setup / Results / Sessions. Setup holds
a resizable splitter, form on the left and viewer on the right.

Stage 16.3 builds the shell only. Each region shows which stage fills it,
and :class:`PageParts` hands those stages the elements to fill.
"""
from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from ui.core.state import GuiSession

PAGE_TITLE = "Molecular Docking Platform"
TAB_SETUP = "Setup"
TAB_RESULTS = "Results"
TAB_SESSIONS = "Sessions"


@dataclass
class PageParts:
    """The page's containers, for the stages that fill them."""

    chat_drawer: ui.right_drawer
    chat_messages: ui.column
    settings_dialog: ui.dialog
    tabs: ui.tabs
    tab_panels: ui.tab_panels
    setup_splitter: ui.splitter


def build_page(state: GuiSession) -> PageParts:
    """Draw the whole page for one client. ``state`` is that client's own."""
    settings_dialog = _settings_dialog()
    chat_drawer, chat_messages = _chat_drawer(state)
    _header(chat_drawer, settings_dialog)
    tabs, tab_panels, setup_splitter = _main_tabs()
    return PageParts(chat_drawer, chat_messages, settings_dialog, tabs, tab_panels, setup_splitter)


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
    with ui.dialog() as dialog, ui.card().classes("min-w-80"):
        ui.label("LLM settings").classes("text-lg font-medium")
        ui.label("Provider and model settings arrive in Stage 16.7.").classes("text-grey-7")
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")
    return dialog


def _chat_drawer(state: GuiSession) -> tuple[ui.right_drawer, ui.column]:
    with ui.right_drawer(value=True).props("bordered width=420") as drawer:
        ui.label("Chat").classes("text-lg font-medium")
        with ui.column().classes("w-full") as messages:
            for message in state.messages:
                # Markdown, as Streamlit's st.chat_message renders it.
                with ui.chat_message(sent=message["role"] == "user").classes("w-full"):
                    ui.markdown(message["content"])
        ui.label("The chat input arrives in Stage 16.4.").classes("text-grey-7")
    return drawer, messages


def _main_tabs() -> tuple[ui.tabs, ui.tab_panels, ui.splitter]:
    with ui.tabs().classes("w-full") as tabs:
        ui.tab(TAB_SETUP)
        ui.tab(TAB_RESULTS)
        ui.tab(TAB_SESSIONS)
    with ui.tab_panels(tabs, value=TAB_SETUP).classes("w-full") as panels:
        with ui.tab_panel(TAB_SETUP):
            with ui.splitter(value=45).classes("w-full").style("min-height: 70vh") as splitter:
                with splitter.before:
                    ui.label("Input, parameters and docking box arrive in Stage 16.4.").classes("p-4")
                with splitter.after:
                    ui.label("The 3D viewer and box picker arrive in Stage 16.5.").classes("p-4")
        with ui.tab_panel(TAB_RESULTS):
            ui.label("Results arrive in Stage 16.6.")
        with ui.tab_panel(TAB_SESSIONS):
            ui.label("Session history arrives in Stage 16.7.")
    return tabs, panels, splitter
