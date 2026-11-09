"""The settings dialog: the LLM provider, endpoint,
model and API key.

The fields are filled from the session's settings each time the dialog
opens. With Ollama, a running Ollama is looked for
(``ui.ollama_models.find_ollama``) and the model is picked from
the models it lists; that search runs off the event loop (``run.io_bound``)
when the dialog opens, when Ollama is picked, when the endpoint field is
committed (Enter or leaving it) and on Re-check. A result for an endpoint no
longer in the field is dropped. If the endpoint is a default one and Ollama
answers elsewhere (the bundled container, the app on the host), the field
takes that endpoint. If none answers, the dialog says how to start one, and
the model is a free-text field.

A found Ollama can download models: the pull runs in
``run.io_bound``, its progress is stored by the worker thread and drawn by a
timer, Cancel stops it, and Apply waits until it ends. Closing the dialog
doesn't stop it; closing the page does.

Apply builds the client and saves the settings file off the event loop
(``ui.core.llm_settings.persist_settings``); the API key is saved only when
"Remember API key" is ticked.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from nicegui import background_tasks, run, ui

from services.llm_orchestrator import NO_LLM_PROVIDER
from ui import ollama_models
from ui.core import llm_settings
from ui_nice.controller import PageController

logger = logging.getLogger(__name__)

APPLIED = "Settings applied."
ENDPOINT_HELP = "Point at a different Ollama server (e.g. a GPU workstation) instead of the bundled one."
FOUND = "Ollama found at {endpoint}."
NO_MODELS = "No models downloaded yet — download one below."
MISSING = "Ollama isn't running at {endpoint}. Start one, then press Re-check:"
START_BUNDLED = "docker compose --profile local-llm up -d ollama"
START_BUNDLED_HELP = "The bundled Ollama container (runs on the CPU; the image is about 4.8 GB)."
OLLAMA_APP_URL = "https://ollama.com/download"
OLLAMA_APP_HELP = "on this computer instead (uses the GPU or Apple Metal)."
DOWNLOADED = "Downloaded {name}. Pick it and press Apply."
CANCELLED = "Download cancelled."
REMEMBER_HELP = "Saved in plain text in the platform's cache folder, readable only by the GUI's user."
# How often the download's progress is drawn.
PROGRESS_INTERVAL_S = 0.5


class SettingsView:
    def __init__(self, controller: PageController) -> None:
        self.controller = controller
        self.state = controller.state
        # The Ollama models listed for the endpoint they were fetched from;
        # None while a fetch runs, before the first one, or when no Ollama
        # answered.
        self._models: Optional[list[str]] = None
        self._models_endpoint: Optional[str] = None
        # True once the search for _models_endpoint has ended.
        self.searched = False
        self.fetching = False
        self.applying = False
        # The model download: the name, the worker's last progress
        # (status, completed, total) and the Cancel flag.
        self.downloading: Optional[str] = None
        self._progress: tuple = ("", 0, 0)
        self._cancel = threading.Event()
        # The models the download choices were last built for.
        self._download_options_for: Optional[list[str]] = None
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
                ui.label("Looking for Ollama...").classes("text-grey-7")
            self.ollama_status = ui.label().classes("text-caption text-grey-7").mark("settings-ollama-status")
            with ui.column().classes("w-full gap-1").mark("settings-ollama-missing") as self.ollama_missing:
                self.missing_text = ui.label().classes("text-caption")
                ui.code(START_BUNDLED, language="bash").classes("w-full")
                ui.label(START_BUNDLED_HELP).classes("text-caption text-grey-7")
                with ui.row().classes("items-center gap-1"):
                    ui.link("Install the Ollama app", OLLAMA_APP_URL, new_tab=True).mark("settings-ollama-app")
                    ui.label(OLLAMA_APP_HELP).classes("text-caption text-grey-7")
                ui.button("Re-check", icon="refresh", on_click=self._on_recheck).props("flat dense").mark(
                    "settings-ollama-recheck"
                )
            self.model_select = ui.select([], label="Model").classes("w-full").mark("settings-model-select")
            self.model_input = ui.input("Model").classes("w-full").mark("settings-model")
            with ui.column().classes("w-full gap-1").mark("settings-download-section") as self.download_section:
                with ui.row().classes("w-full items-center no-wrap"):
                    self.download_model = ui.select(
                        {}, label="Download a model", with_input=True, new_value_mode="add-unique",
                    ).classes("grow").mark("settings-download-model")
                    self.download_button = ui.button("Download", icon="download", on_click=self._on_download).props(
                        "flat"
                    ).mark("settings-download")
                with ui.row().classes("w-full items-center no-wrap") as self.download_running:
                    self.download_bar = ui.linear_progress(value=0, show_value=False).classes("grow").mark(
                        "settings-download-progress"
                    )
                    ui.button("Cancel", on_click=self._on_cancel_download).props("flat dense").mark(
                        "settings-download-cancel"
                    )
                self.download_status = ui.label().classes("text-caption text-grey-7").mark(
                    "settings-download-status"
                )
            self.api_key = ui.input("API Key", password=True, password_toggle_button=True).classes(
                "w-full"
            ).mark("settings-api-key")
            self.remember_key = ui.checkbox("Remember API key on this computer").tooltip(REMEMBER_HELP).mark(
                "settings-remember-key"
            )
            self.message = ui.label().mark("settings-message")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=self.dialog.close).props("flat").mark("settings-close")
                self.apply_button = ui.button("Apply settings", on_click=self._on_apply).mark("settings-apply")
        self.progress_timer = ui.timer(PROGRESS_INTERVAL_S, self._draw_progress, active=False)
        # A download nobody can follow or cancel any more is stopped, so it
        # doesn't hold the one-download lock for every other page.
        ui.context.client.on_delete(self._cancel.set)
        self._fill()

    def _endpoint_input(self, marker: str) -> ui.input:
        element = ui.input("Endpoint URL").classes("w-full").mark(marker)
        element.on("blur", self._on_endpoint_commit)
        element.on("keydown.enter", self._on_endpoint_commit)
        return element

    def open(self) -> None:
        """Open the dialog, filled from the session's settings, and look for
        Ollama afresh."""
        self._models, self._models_endpoint, self.searched = None, None, False
        self._fill()
        self.dialog.open()
        background_tasks.create(self._fetch_models(), name="find ollama")

    # ── Fields ───────────────────────────────────────────────────────────────

    def _fill(self) -> None:
        """The fields, from the session's settings. A running download keeps
        its own state."""
        settings = self.state.settings
        provider = settings.get("provider", NO_LLM_PROVIDER)
        if provider not in llm_settings.PROVIDER_LABELS:
            provider = NO_LLM_PROVIDER
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
            # session's, so an unapplied pick doesn't survive a reopen.
            self.model_select.value = None
            self.api_key.value = settings.get("api_key", "")
            self.remember_key.value = bool(settings.get("remember_api_key"))
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
        takes_key = provider in llm_settings.API_KEY_PROVIDERS
        self.api_key.set_visibility(takes_key)
        self.remember_key.set_visibility(takes_key)

        current_result = (
            ollama and self.searched and not self.fetching and self._models_endpoint == self._endpoint_value()
        )
        found = current_result and self._models is not None
        models = self._models if found else None
        listed = bool(models)
        self.models_loading.set_visibility(ollama and self.fetching)
        self.ollama_status.set_visibility(found)
        if found:
            self.ollama_status.set_text(FOUND.format(endpoint=self._models_endpoint) + ("" if listed else f" {NO_MODELS}"))
        self.ollama_missing.set_visibility(current_result and not found)
        if current_result and not found:
            self.missing_text.set_text(MISSING.format(endpoint=self._models_endpoint))
        self.model_select.set_visibility(ollama and listed)
        self.model_input.set_visibility(provider != NO_LLM_PROVIDER and not (ollama and listed))
        if listed:
            current = self.model_input.value or ""
            options = models if current in models else ([current] if current else []) + models
            if self.model_select.options != options:
                self.model_select.set_options(options)
            if self.model_select.value not in options:
                self.model_select.value = current if current in options else options[0]

        # The download section stays visible while a download runs, so it
        # can be followed and cancelled even after the endpoint changed.
        downloading = self.downloading is not None
        self.download_section.set_visibility((ollama and found) or downloading)
        # Rebuilt only when the listed models change: a name the user typed
        # in is added to the options, and must survive the next _sync.
        if found and self._download_options_for != models:
            self._download_options_for = list(models or [])
            self.download_model.set_options(llm_settings.download_options(self._download_options_for))
        self.download_running.set_visibility(downloading)
        self.download_button.set_enabled(not downloading and found)
        self.download_model.set_enabled(not downloading)
        self.apply_button.set_enabled(not (self.applying or self.fetching or downloading))

    def _model_value(self) -> str:
        if not self.model_select.visible:
            return self.model_input.value or ""
        return self.model_select.value or ""

    async def _fetch_models(self, force: bool = False) -> None:
        endpoint = self._endpoint_value()
        already = self._models_endpoint == endpoint and (self.searched or self.fetching)
        if self.provider.value != "ollama" or (already and not force):
            self._sync()
            return
        self._models, self._models_endpoint = None, endpoint
        self.searched = False
        self.fetching = True
        self._sync()
        try:
            # Looked up at call time, so a test's patch of the module reaches it.
            found = await run.io_bound(ollama_models.find_ollama, endpoint)
        finally:
            if self._models_endpoint == endpoint:
                self.fetching = False
        if self._models_endpoint != endpoint:
            return  # stale: the endpoint changed meanwhile
        self.searched = True
        if found is None:
            self._models = None
        else:
            found_endpoint, self._models = found
            if found_endpoint != endpoint:
                # The search found Ollama elsewhere: the field takes it,
                # applied (and saved) by Apply like a typed one.
                self.ollama_endpoint.value = found_endpoint
                self._models_endpoint = found_endpoint
        self._sync()

    # ── Model download ───────────────────────────────────────────────────────

    def _record_progress(self, status: str, completed: int, total: int) -> None:
        """Called on the download's worker thread: only stores, never draws."""
        self._progress = (status, completed, total)

    def _draw_progress(self) -> None:
        status, completed, total = self._progress
        self.download_bar.set_value(completed / total if total else 0)
        size = f" — {completed / 1e9:.1f} / {total / 1e9:.1f} GB" if total else ""
        self.download_status.set_text(f"{self.downloading}: {status}{size}" if self.downloading else "")

    async def _on_download(self) -> None:
        # Known issue: a typed name becomes the
        # select's value only on Enter, so without it Download does nothing.
        name = (self.download_model.value or "").strip()
        if not name or self.downloading is not None:
            return
        endpoint = self._models_endpoint or self._endpoint_value()
        self.downloading, self._progress = name, ("starting", 0, 0)
        self._cancel.clear()
        self.message.set_text("")
        self.progress_timer.activate()
        self._sync()
        self._draw_progress()
        finished = False
        try:
            # Looked up at call time, so a test's patch of the module reaches it.
            finished = await run.io_bound(
                ollama_models.pull_model, endpoint, name, self._record_progress, self._cancel.is_set,
            )
        except Exception as exc:
            # The GUI's boundary for the download: any failure is shown.
            logger.warning("downloading Ollama model %s failed", name, exc_info=True)
            self._show(f"Download failed: {exc}", "text-negative")
        else:
            if finished:
                # The new model becomes the pick once it is listed (_sync
                # keeps a still-listed pick otherwise) -- unless the user
                # has moved to another provider meanwhile, whose model field
                # it would overwrite.
                if self.provider.value == "ollama":
                    self.model_input.value = name
                    self.model_select.value = None
                self.download_model.value = None
                self._show(DOWNLOADED.format(name=name), "text-positive")
            else:
                self._show(CANCELLED, "text-grey-7")
        finally:
            self.downloading = None
            self.progress_timer.deactivate()
            self._draw_progress()
        if finished:
            # Listed again, so the new model is offered and selected.
            await self._fetch_models(force=True)
        else:
            self._sync()

    def _on_cancel_download(self) -> None:
        self._cancel.set()

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _show(self, text: str, color: str) -> None:
        self.message.set_text(text)
        self.message.classes(replace=color)

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
        background_tasks.create(self._fetch_models(), name="find ollama")

    async def _on_endpoint_commit(self) -> None:
        await self._fetch_models()

    async def _on_recheck(self) -> None:
        await self._fetch_models(force=True)

    async def _on_apply(self) -> None:
        new_settings = llm_settings.settings_from_form(
            self.provider.value, self._model_value(), self._endpoint_value(),
            self.api_key.value or "", self.base_url.value or "",
            remember_api_key=bool(self.remember_key.value),
        )
        self.applying = True
        self._sync()
        try:
            client = await run.io_bound(llm_settings.persist_settings, new_settings)
        except Exception as exc:
            # Any failure to build or save is shown.
            logger.warning("applying the LLM settings failed", exc_info=True)
            self._show(f"Could not apply settings: {exc}", "text-negative")
        else:
            llm_settings.use_settings(self.state, new_settings, client)
            self.model_input.value = new_settings["model"]
            self._show(APPLIED, "text-positive")
            # Other views follow the client (the chat's setup hint).
            self.controller.sync()
        finally:
            self.applying = False
            self._sync()
