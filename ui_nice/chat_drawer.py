"""The chat drawer (Phase 16, Stage 16.4): the session label, the messages,
the docking run's log and the chat input.

Commands are answered by ``ui.core.chat.submit``; the two that do slow
file work (geometry, ``save ... as``) run it off the event loop through the
controller. A run command ("run", "redock", "dock with vina") does what
Run Docking does, through ``controller.run_docking`` (Stage 17.1). Any other message goes to the LLM: the blocking call runs in
``run.io_bound`` with the input disabled, and its result is applied only if
the session hasn't changed meanwhile (``chat.parse_context``). A save
disables the input the same way (``controller.busy``). With no LLM set up
(provider "none"), a "Set up an LLM to chat" button above the input opens
the settings dialog (Stage 18.3).
"""
from __future__ import annotations

from typing import Callable

from nicegui import run, ui

from services.llm_orchestrator import workflow_llm_parser
from ui.core import chat, results_view
from ui_nice.controller import PageController
from ui_nice.widgets import markdown

# The Run log keeps this many of the run's last lines, like the Session Log.
RUN_LOG_LINES = results_view.JOB_LOG_TAIL_LINES
PARSING = "Parsing with LLM..."
LLM_HINT = "Set up an LLM to chat"


class ChatView:
    def __init__(self, controller: PageController, open_settings: Callable[[], None]) -> None:
        self.controller = controller
        self.state = controller.state
        self.parsing = False
        # What is drawn: the messages list and run log by identity, and how
        # many of their items. A replaced list (New Session, a new run) is
        # drawn afresh; a grown one gets only its new items.
        self._drawn_messages: list | None = None
        self._drawn_message_count = 0
        self._drawn_log: list | None = None
        self._drawn_log_count = 0

        self.session_label = markdown().classes("text-caption text-grey-7").mark("chat-session")
        ui.label("Chat").classes("text-lg font-medium")
        self.messages = ui.column().classes("w-full")
        with ui.expansion("Run log").classes("w-full").mark("run-log") as self.log_expansion:
            self.log = ui.log(max_lines=RUN_LOG_LINES).classes("w-full h-64")
        self.llm_hint = ui.button(LLM_HINT, icon="settings", on_click=open_settings).props(
            "flat dense no-caps"
        ).mark("chat-llm-hint")
        with ui.row().classes("w-full items-center no-wrap"):
            self.input = (
                ui.input(placeholder="Describe your workflow...")
                .props("outlined dense")
                .classes("grow")
                .mark("chat-input")
                .on("keydown.enter", self._on_send)
            )
            self.send = ui.button(icon="send", on_click=self._on_send).props("flat round").mark("chat-send")
        self.spinner_row = ui.row().classes("items-center")
        with self.spinner_row:
            ui.spinner(size="sm")
            self.spinner_label = ui.label(PARSING).classes("text-grey-7").mark("chat-busy")
        controller.add_view(self)
        self.sync()

    def sync(self) -> None:
        state = self.state
        if state.job_id:
            self.session_label.set_content(f"Session: {results_view.job_label(state.job_id, state.job_name)}")
        self.session_label.set_visibility(bool(state.job_id))
        self._sync_messages()
        self._sync_log()
        self.llm_hint.set_visibility(state.llm_client is None)
        busy = PARSING if self.parsing else self.controller.busy
        self.input.set_enabled(busy is None)
        self.send.set_enabled(busy is None)
        self.spinner_label.set_text(busy or "")
        self.spinner_row.set_visibility(busy is not None)

    async def poll(self) -> None:
        pass  # draws only the state

    def _sync_messages(self) -> None:
        messages = self.state.messages
        if messages is not self._drawn_messages or len(messages) < self._drawn_message_count:
            self.messages.clear()
            self._drawn_messages, self._drawn_message_count = messages, 0
        if len(messages) == self._drawn_message_count:
            return
        with self.messages:
            for message in messages[self._drawn_message_count:]:
                # Replies are markdown (bold names, code spans, lists).
                with ui.chat_message(sent=message["role"] == "user").classes("w-full"):
                    markdown(message["content"])
        self._drawn_message_count = len(messages)

    def _sync_log(self) -> None:
        run_log = self.state.run_log
        if run_log is not self._drawn_log:
            self.log.clear()
            self._drawn_log, self._drawn_log_count = run_log, 0
        for line in run_log[self._drawn_log_count:]:
            self.log.push(line)
        self._drawn_log_count = len(run_log)
        self.log_expansion.set_visibility(bool(run_log))

    async def _on_send(self) -> None:
        message = (self.input.value or "").strip()
        controller, state = self.controller, self.state
        if not message or self.parsing or controller.busy:
            return
        self.input.value = ""
        controller.start_action()
        if chat.submit(
            state, message, controller.output_base, controller.bridge_factory,
            apply_geometry=controller.apply_geometry, run_save=controller.run_save,
            run_docking=controller.run_docking,
        ):
            controller.sync()
            return

        self.parsing = True
        controller.sync()
        try:
            context = chat.parse_context(state)
            parsed, named = await run.io_bound(
                chat.parse_message,
                state.llm_client,
                message,
                state.parser.parsed_workflow,
                # Looked up now, so a test's patch of the module reaches them.
                parse_workflow=workflow_llm_parser.parse_workflow_with_llm,
                parse_followup=workflow_llm_parser.parse_followup_with_llm,
            )
            chat.apply_parsed_message(state, parsed, named, context=context)
        except Exception as exc:
            # The GUI's boundary for the LLM parse: any failure is the reply.
            chat.parse_failed(state, exc)
        finally:
            self.parsing = False
        controller.sync()
