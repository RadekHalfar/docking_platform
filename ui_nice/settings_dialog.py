"""The settings dialog (Phase 16, Stage 16.7): the LLM provider, endpoint,
model and API key.

The fields are filled from the session's settings each time the dialog
opens. With Ollama, the model is picked from the models the endpoint
lists; that list is fetched off the event loop (``run.io_bound``) when the
dialog opens, when Ollama is picked, and when the endpoint field is
committed (Enter or leaving it). A fetch for an endpoint no longer in the
field is dropped. If the list is empty, the model is a free-text field.

Apply builds the client and saves the settings file off the event loop
(``ui.core.llm_settings.persist_settings``); the API key is never saved.
"""
from __future__ import annotations

import logging
from typing import Optional

from nicegui import background_tasks, run, ui

from ui import ollama_models
from ui.core import llm_settings
from ui_nice.controller import PageController

logger = logging.getLogger(__name__)

MODELS_MISSING = "Could not reach Ollama to list models — showing free-text input."
APPLIED = "Settings applied."
ENDPOINT_HELP = "Point at a different Ollama server (e.g. a GPU workstation) instead of the bundled one."


class SettingsView:
    def __init__(self, controller: PageController) -> None:
        self.controller = controller
        self.state = controller.state
        # The Ollama models listed for the endpoint they were fetched from;
        # None while a fetch runs or before the first one.
        self._models: Optional[list[str]] = None
        self._models_endpoint: Optional[str] = None
        self.fetching = False
        self.applying = False
        # True while _fill writes the fields: their change handlers ignore it.
        self._filling = False

        with ui.dialog().mark("settings-dialog") as self.dialog, ui.card().classes("min-w-96"):
            ui.label("LLM settings").classes("text-lg font-medium")
            self.provider = ui.select(
                dict(llm_settings.PROVIDER_LABELS), label="LLM Provider", on_change=self._on_provider,
            ).classes("w-full").mark("settings-provider")
            with ui.expansion("Advanced").classes("w-full").mark("settings-advanced") as self.advanced:
                self.ollama_endpoint = self._endpoint_input("settings-ollama-endpoint").tooltip(ENDPOINT_HELP)
            self.endpoint = self._endpoint_input("settings-endpoint")
            self.base_url = ui.input("Base URL (OpenAI-compatible)").classes("w-full").mark("settings-base-url")
            with ui.row().classes("items-center") as self.models_loading:
                ui.spinner(size="sm")
                ui.label("Checking available Ollama models...").classes("text-grey-7")
            self.model_select = ui.select([], label="Model").classes("w-full").mark("settings-model-select")
            self.model_input = ui.input("Model").classes("w-full").mark("settings-model")
            self.models_missing = ui.label(MODELS_MISSING).classes("text-caption text-grey-7").mark(
                "settings-models-missing"
            )
            self.api_key = ui.input("API Key", password=True, password_toggle_button=True).classes(
                "w-full"
            ).mark("settings-api-key")
            self.message = ui.label().mark("settings-message")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=self.dialog.close).props("flat").mark("settings-close")
                self.apply_button = ui.button("Apply settings", on_click=self._on_apply).mark("settings-apply")
        self._fill()

    def _endpoint_input(self, marker: str) -> ui.input:
        element = ui.input("Endpoint URL").classes("w-full").mark(marker)
        element.on("blur", self._on_endpoint_commit)
        element.on("keydown.enter", self._on_endpoint_commit)
        return element

    def open(self) -> None:
        """Open the dialog, filled from the session's settings, and list the
        Ollama models afresh."""
        self._models, self._models_endpoint = None, None
        self._fill()
        self.dialog.open()
        background_tasks.create(self._fetch_models(), name="list ollama models")

    # ── Fields ───────────────────────────────────────────────────────────────

    def _fill(self) -> None:
        """The fields, from the session's settings."""
        settings = self.state.settings
        provider = settings.get("provider", "ollama")
        if provider not in llm_settings.PROVIDER_LABELS:
            provider = "ollama"
        endpoint = settings.get("endpoint", "") or llm_settings.DEFAULT_ENDPOINT
        self._filling = True
        try:
            self.provider.value = provider
            self.ollama_endpoint.value = self.endpoint.value = endpoint
            self.base_url.value = (
                settings.get("base_url", "") if provider == "openai_compatible" else ""
            ) or endpoint
            self.model_input.value = settings.get("model", "")
            # A model picked but not applied is dropped; _sync selects the
            # session's (an unapplied pick survived a reopen, Review Stage R3).
            self.model_select.value = None
            self.api_key.value = settings.get("api_key", "")
        finally:
            self._filling = False
        self.message.set_text("")
        self._sync()

    def _endpoint_value(self) -> str:
        field = self.ollama_endpoint if self.provider.value == "ollama" else self.endpoint
        return field.value or ""

    def _sync(self) -> None:
        provider = self.provider.value
        ollama = provider == "ollama"
        self.advanced.set_visibility(ollama)
        self.endpoint.set_visibility(provider == "openai_compatible")
        self.base_url.set_visibility(provider == "openai_compatible")
        self.api_key.set_visibility(provider in llm_settings.API_KEY_PROVIDERS)

        models = self._models if ollama and self._models_endpoint == self._endpoint_value() else None
        listed = bool(models)
        self.models_loading.set_visibility(ollama and self.fetching)
        self.model_select.set_visibility(ollama and listed)
        self.model_input.set_visibility(not (ollama and listed))
        self.models_missing.set_visibility(ollama and not self.fetching and models == [])
        if listed:
            current = self.model_input.value or ""
            options = models if current in models else ([current] if current else []) + models
            if self.model_select.options != options:
                self.model_select.set_options(options)
            if self.model_select.value not in options:
                self.model_select.value = current if current in options else options[0]
        self.apply_button.set_enabled(not (self.applying or self.fetching))

    def _model_value(self) -> str:
        if not self.model_select.visible:
            return self.model_input.value or ""
        return self.model_select.value or ""

    async def _fetch_models(self) -> None:
        endpoint = self._endpoint_value()
        listed_or_listing = self._models is not None or self.fetching
        if self.provider.value != "ollama" or (self._models_endpoint == endpoint and listed_or_listing):
            self._sync()
            return
        self._models, self._models_endpoint = None, endpoint
        self.fetching = True
        self._sync()
        try:
            # Looked up at call time, so a test's patch of the module reaches it.
            models = await run.io_bound(ollama_models.list_ollama_models, endpoint)
        finally:
            if self._models_endpoint == endpoint:
                self.fetching = False
        if self._models_endpoint != endpoint:
            return  # stale: the endpoint changed meanwhile
        self._models = models
        self._sync()

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_provider(self, event) -> None:
        if self._filling:
            return
        self.message.set_text("")
        # The two endpoint fields show one value, whichever is visible.
        if event.value == "ollama":
            self.ollama_endpoint.value = self.endpoint.value
        else:
            self.endpoint.value = self.ollama_endpoint.value
        self._sync()
        background_tasks.create(self._fetch_models(), name="list ollama models")

    async def _on_endpoint_commit(self) -> None:
        await self._fetch_models()

    async def _on_apply(self) -> None:
        new_settings = llm_settings.settings_from_form(
            self.provider.value, self._model_value(), self._endpoint_value(),
            self.api_key.value or "", self.base_url.value or "",
        )
        self.applying = True
        self._sync()
        try:
            client = await run.io_bound(llm_settings.persist_settings, new_settings)
        except Exception as exc:
            # Any failure to build or save is shown.
            logger.warning("applying the LLM settings failed", exc_info=True)
            self.message.set_text(f"Could not apply settings: {exc}")
            self.message.classes(replace="text-negative")
        else:
            llm_settings.use_settings(self.state, new_settings, client)
            self.model_input.value = new_settings["model"]
            self.message.set_text(APPLIED)
            self.message.classes(replace="text-positive")
        finally:
            self.applying = False
            self._sync()
