"""The NiceGUI settings dialog (Phase 16, Stage 16.7): the LLM provider,
endpoint, model and API key; Ollama detection, model download and the
remembered API key (Stage 18.3). Runs in the GUI test tier only (see
tests/test_nicegui_setup.py).
"""
import json
import os
import threading

import pytest

pytest.importorskip("nicegui")

from nicegui.testing import User  # noqa: E402

from ui.core import llm_settings  # noqa: E402
from ui_nice import settings_dialog  # noqa: E402
from tests.nicegui_harness import click, element, eventually, on_event_loop, set_value  # noqa: E402

MODELS = ["gemma3:4b", "qwen2.5-coder:7b"]


def _view(page):
    return page.parts[-1].settings


async def _models_listed(page):
    """Wait until the dialog's search for Ollama (off the event loop) ended."""
    view = _view(page)
    await eventually(lambda: not view.fetching and view.searched)


async def _open_dialog(user, page):
    await user.open("/")
    click(user, "settings-button")
    await _models_listed(page)


async def _apply(user, page):
    click(user, "settings-apply")
    view = _view(page)

    def applied() -> bool:
        text = element(user, "settings-message").text
        return not view.applying and (text == settings_dialog.APPLIED or text.startswith("Could not apply"))

    await eventually(applied)


@pytest.fixture
def ollama_lists(monkeypatch):
    """Ollama lists MODELS; records the endpoints asked."""
    asked = []

    def list_models(endpoint, timeout=3.0):
        asked.append((endpoint, on_event_loop()))
        return list(MODELS)

    monkeypatch.setattr("ui.ollama_models.list_ollama_models", list_models)
    return asked


async def test_dialog_lists_the_ollama_models_off_the_event_loop(user: User, page, ollama_lists):
    await _open_dialog(user, page)

    select = element(user, "settings-model-select")
    assert select.visible and not element(user, "settings-model").visible
    # The session's model (from the environment) is kept as the first option.
    current = page.state.settings["model"]
    assert select.options == [current, *MODELS] and select.value == current
    assert ollama_lists == [("http://127.0.0.1:9", False)]
    assert not element(user, "settings-api-key").visible


async def test_unreachable_ollama_says_how_to_start_it(user: User, page):
    # No Ollama answers in the harness.
    await _open_dialog(user, page)

    assert element(user, "settings-model").visible
    assert not element(user, "settings-model-select").visible
    assert element(user, "settings-ollama-missing").visible
    assert not element(user, "settings-ollama-status").visible
    assert not element(user, "settings-download-section").visible
    assert "http://127.0.0.1:9" in _view(page).missing_text.text
    assert element(user, "settings-ollama-app").props["href"] == settings_dialog.OLLAMA_APP_URL


async def test_recheck_finds_ollama_once_it_runs(user: User, page, monkeypatch):
    answers = {"models": None}
    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: answers["models"])
    await _open_dialog(user, page)
    assert element(user, "settings-ollama-missing").visible

    answers["models"] = list(MODELS)
    click(user, "settings-ollama-recheck")
    await eventually(lambda: element(user, "settings-ollama-status").visible)

    assert not element(user, "settings-ollama-missing").visible
    assert element(user, "settings-ollama-status").text == settings_dialog.FOUND.format(endpoint="http://127.0.0.1:9")
    assert element(user, "settings-model-select").visible


async def test_ollama_found_elsewhere_takes_the_endpoint(user: User, page, monkeypatch):
    # The session's endpoint is the bundled one, which isn't running; the
    # Ollama app on the host answers.
    monkeypatch.setenv("LLM_ENDPOINT", "http://ollama:11434")
    host = "http://host.docker.internal:11434"
    monkeypatch.setattr(
        "ui.ollama_models.list_ollama_models", lambda endpoint, **k: list(MODELS) if endpoint == host else None,
    )
    await _open_dialog(user, page)

    assert element(user, "settings-ollama-endpoint").value == host
    assert element(user, "settings-ollama-status").text == settings_dialog.FOUND.format(endpoint=host)
    await _apply(user, page)
    assert page.state.settings["endpoint"] == host


async def test_ollama_without_models_offers_the_recommended_downloads(user: User, page, monkeypatch):
    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: [])
    await _open_dialog(user, page)

    assert settings_dialog.NO_MODELS in element(user, "settings-ollama-status").text
    assert element(user, "settings-model").visible
    assert element(user, "settings-download-section").visible
    assert list(element(user, "settings-download-model").options) == [
        name for name, _, _ in llm_settings.RECOMMENDED_OLLAMA_MODELS
    ]


async def test_a_committed_endpoint_lists_its_own_models(user: User, page, ollama_lists):
    await _open_dialog(user, page)
    element(user, "settings-advanced").value = True

    set_value(user, "settings-ollama-endpoint", "http://gpu-box:11434")
    user.find(marker="settings-ollama-endpoint").trigger("blur")
    await eventually(lambda: len(ollama_lists) == 2 and not _view(page).fetching)

    assert ollama_lists[-1] == ("http://gpu-box:11434", False)


async def test_each_provider_shows_its_own_fields(user: User, page):
    await _open_dialog(user, page)

    set_value(user, "settings-provider", "openai")
    assert element(user, "settings-api-key").visible
    assert not element(user, "settings-base-url").visible
    assert not element(user, "settings-advanced").visible
    assert element(user, "settings-model").visible

    set_value(user, "settings-provider", "openai_compatible")
    assert element(user, "settings-endpoint").visible and element(user, "settings-base-url").visible
    assert element(user, "settings-api-key").visible

    set_value(user, "settings-provider", "ollama")
    assert element(user, "settings-advanced").visible
    assert not element(user, "settings-api-key").visible


async def test_apply_saves_the_settings_and_rebuilds_the_client(user: User, page, tmp_path, monkeypatch):
    where = []
    real = llm_settings.persist_settings

    def persist(new_settings):
        where.append(on_event_loop())
        return real(new_settings)

    monkeypatch.setattr(llm_settings, "persist_settings", persist)
    await _open_dialog(user, page)
    old_client = page.state.llm_client

    set_value(user, "settings-provider", "openai")
    set_value(user, "settings-model", " gpt-x ")
    set_value(user, "settings-api-key", "sk-test")
    await _apply(user, page)

    assert element(user, "settings-message").text == settings_dialog.APPLIED
    assert where == [False]
    state = page.state
    assert state.settings["provider"] == "openai" and state.settings["model"] == "gpt-x"
    assert state.settings["api_key"] == "sk-test"
    assert state.llm_client is not old_client
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["provider"] == "openai" and saved["model"] == "gpt-x"
    assert "api_key" not in saved


async def test_apply_refuses_a_provider_without_a_model(user: User, page, tmp_path):
    # LLM_MODEL is empty by default since Stage 18.2, so a client with model
    # "" was applied and every chat message then failed (Review Stage R5).
    await _open_dialog(user, page)
    before, client = dict(page.state.settings), page.state.llm_client

    set_value(user, "settings-provider", "openai")
    set_value(user, "settings-model", "  ")
    set_value(user, "settings-api-key", "sk-test")
    await _apply(user, page)

    assert element(user, "settings-message").text == (
        "Could not apply settings: " + llm_settings.MODEL_REQUIRED.format(provider="OpenAI")
    )
    assert page.state.settings == before and page.state.llm_client is client
    assert not (tmp_path / "settings.json").exists()


async def test_rejected_settings_change_nothing(user: User, page, tmp_path, monkeypatch):
    def reject(settings):
        raise ValueError("no such provider")

    monkeypatch.setattr(llm_settings, "build_llm_client", reject)
    await _open_dialog(user, page)
    before, client = dict(page.state.settings), page.state.llm_client

    set_value(user, "settings-provider", "anthropic")
    await _apply(user, page)

    assert element(user, "settings-message").text == "Could not apply settings: no such provider"
    assert page.state.settings == before and page.state.llm_client is client
    assert not (tmp_path / "settings.json").exists()


async def test_reopening_the_dialog_shows_the_session_settings(user: User, page):
    await _open_dialog(user, page)
    set_value(user, "settings-provider", "anthropic")
    click(user, "settings-close")

    click(user, "settings-button")
    await _models_listed(page)

    assert element(user, "settings-provider").value == "ollama"


async def test_reopening_drops_a_model_picked_but_not_applied(user: User, page, ollama_lists):
    # The select kept its value while it was still listed, so a later Apply
    # (say, of a new endpoint) saved the unapplied model (Review Stage R3).
    await _open_dialog(user, page)
    current = page.state.settings["model"]
    set_value(user, "settings-model-select", MODELS[1])
    click(user, "settings-close")

    click(user, "settings-button")
    await _models_listed(page)

    assert element(user, "settings-model-select").value == current


# ---------------------------------------------------------------------------
# No LLM (provider "none", the Phase 18 default)
# ---------------------------------------------------------------------------

async def test_no_llm_default_shows_only_the_provider(user: User, page, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "")
    await user.open("/")
    click(user, "settings-button")  # nothing to list: no Ollama fetch starts

    assert element(user, "settings-provider").value == "none"
    assert page.state.llm_client is None
    for marker in ("settings-model", "settings-model-select", "settings-api-key",
                   "settings-advanced", "settings-endpoint", "settings-base-url"):
        assert not element(user, marker).visible, marker


async def test_applying_none_drops_the_client(user: User, page, tmp_path):
    await _open_dialog(user, page)
    assert page.state.llm_client is not None

    set_value(user, "settings-provider", "none")
    await _apply(user, page)

    assert element(user, "settings-message").text == settings_dialog.APPLIED
    assert page.state.llm_client is None
    assert json.loads((tmp_path / "settings.json").read_text())["provider"] == "none"


# ---------------------------------------------------------------------------
# Model download (Stage 18.3)
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_pull(monkeypatch):
    """Ollama lists a fresh copy of MODELS (a finished download adds to it);
    a download reports progress, then waits until released or cancelled.
    Records (endpoint, name, on the event loop)."""
    models, pulls, release = list(MODELS), [], threading.Event()

    def pull(endpoint, name, on_progress, should_cancel):
        pulls.append((endpoint, name, on_event_loop()))
        on_progress("pulling abc", 1_000_000_000, 4_000_000_000)
        while not release.wait(0.02):
            if should_cancel():
                return False
        models.append(name)
        return True

    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: list(models))
    monkeypatch.setattr("ui.ollama_models.pull_ollama_model", pull)
    yield models, pulls, release
    release.set()


# A recommended model that MODELS doesn't have, so it is offered.
NEW_MODEL = llm_settings.RECOMMENDED_OLLAMA_MODELS[1][0]


def _type_model(user, name):
    """Type a name that isn't offered, as Quasar's new-value event does
    (through NiceGUI's own handler, which adds it to the options)."""
    select = element(user, "settings-download-model")
    select.value = select._handle_new_value(name)


async def _start_download(user, page, name=NEW_MODEL):
    await _open_dialog(user, page)
    if name in element(user, "settings-download-model").options:
        element(user, "settings-download-model").value = name
    else:
        _type_model(user, name)
    click(user, "settings-download")
    view = _view(page)
    # Drawn by the progress timer, from what the worker stored.
    await eventually(lambda: view.downloading == name and "pulling abc" in element(user, "settings-download-status").text)


async def _download_ended(page):
    view = _view(page)
    await eventually(lambda: view.downloading is None and view.searched and not view.fetching)


async def test_download_shows_progress_and_blocks_apply(user: User, page, slow_pull):
    _, pulls, release = slow_pull
    await _start_download(user, page)

    assert pulls == [("http://127.0.0.1:9", NEW_MODEL, False)]
    assert element(user, "settings-download-progress").value == 0.25
    assert element(user, "settings-download-status").text == f"{NEW_MODEL}: pulling abc — 1.0 / 4.0 GB"
    assert not element(user, "settings-apply").enabled
    assert not element(user, "settings-download").enabled

    release.set()
    await _download_ended(page)

    assert element(user, "settings-message").text == settings_dialog.DOWNLOADED.format(name=NEW_MODEL)
    assert element(user, "settings-model-select").value == NEW_MODEL
    # Downloaded, it is no longer offered.
    assert NEW_MODEL not in element(user, "settings-download-model").options
    assert element(user, "settings-apply").enabled


async def test_a_typed_model_is_downloaded_and_applied(user: User, page, slow_pull):
    _, pulls, release = slow_pull
    await _start_download(user, page, "gemma3:1b")
    assert pulls[0][1] == "gemma3:1b"
    release.set()
    await _download_ended(page)

    await _apply(user, page)
    assert page.state.settings["model"] == "gemma3:1b"


async def test_cancel_stops_the_download(user: User, page, slow_pull):
    models, _, _ = slow_pull
    await _start_download(user, page)

    click(user, "settings-download-cancel")
    view = _view(page)
    await eventually(lambda: view.downloading is None)

    assert element(user, "settings-message").text == settings_dialog.CANCELLED
    assert not view.download_running.visible
    assert element(user, "settings-apply").enabled
    assert NEW_MODEL not in models


async def test_a_failed_download_is_shown(user: User, page, ollama_lists, monkeypatch):
    def pull(endpoint, name, on_progress, should_cancel):
        raise RuntimeError("Failed to pull Ollama model nope: file does not exist")

    monkeypatch.setattr("ui.ollama_models.pull_ollama_model", pull)
    await _open_dialog(user, page)
    _type_model(user, "nope")
    click(user, "settings-download")
    view = _view(page)
    await eventually(lambda: view.downloading is None and element(user, "settings-message").text)

    assert element(user, "settings-message").text == (
        "Download failed: Failed to pull Ollama model nope: file does not exist"
    )
    assert element(user, "settings-apply").enabled


async def test_a_download_survives_closing_the_dialog(user: User, page, slow_pull):
    _, _, release = slow_pull
    await _start_download(user, page)
    click(user, "settings-close")

    click(user, "settings-button")
    await _models_listed(page)

    assert _view(page).downloading == NEW_MODEL
    assert _view(page).download_running.visible
    release.set()
    await _download_ended(page)


async def test_a_download_ending_after_a_provider_switch_keeps_the_new_model(user: User, page, slow_pull):
    # The finished download wrote its name into the model field of whatever
    # provider was picked by then (Review Stage R5).
    _, _, release = slow_pull
    await _start_download(user, page)
    set_value(user, "settings-provider", "openai")
    set_value(user, "settings-model", "gpt-x")

    release.set()
    await eventually(lambda: _view(page).downloading is None)

    assert element(user, "settings-message").text == settings_dialog.DOWNLOADED.format(name=NEW_MODEL)
    assert element(user, "settings-model").value == "gpt-x"


async def test_closing_the_page_cancels_its_download(user: User, page, slow_pull):
    # Otherwise it ran on with nothing able to cancel it, and every other
    # page got PULL_BUSY until it ended (Review Stage R5).
    models, _, _ = slow_pull
    await _start_download(user, page)
    view = _view(page)

    view.dialog.client.delete()
    await eventually(lambda: view.downloading is None)

    assert view._cancel.is_set()
    assert NEW_MODEL not in models


# ---------------------------------------------------------------------------
# Remembered API key (Stage 18.3)
# ---------------------------------------------------------------------------

async def test_a_remembered_key_is_saved_with_mode_0600_and_reloaded(user: User, page, tmp_path):
    await _open_dialog(user, page)
    set_value(user, "settings-provider", "openai")
    remember = element(user, "settings-remember-key")
    assert remember.visible and not remember.value
    set_value(user, "settings-model", "gpt-x")
    set_value(user, "settings-api-key", "sk-remembered")
    set_value(user, "settings-remember-key", True)
    await _apply(user, page)

    path = tmp_path / "settings.json"
    saved = json.loads(path.read_text())
    assert saved["api_key"] == "sk-remembered" and saved["remember_api_key"] is True
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600

    # A new page (a restart, another tab) gets the key back, ticked.
    await user.open("/")
    assert page.state.settings["api_key"] == "sk-remembered"
    click(user, "settings-button")
    assert element(user, "settings-remember-key").value is True

    # Unticked, the key leaves the file.
    set_value(user, "settings-remember-key", False)
    await _apply(user, page)
    saved = json.loads(path.read_text())
    assert "api_key" not in saved and saved["remember_api_key"] is False


async def test_a_remembered_key_is_dropped_for_a_provider_without_one(user: User, page, tmp_path):
    # The key and "Remember" fields are only hidden for Ollama and None, so
    # Apply saved the old key again, with no way to untick it (Review Stage R5).
    await _open_dialog(user, page)
    set_value(user, "settings-provider", "openai")
    set_value(user, "settings-model", "gpt-x")
    set_value(user, "settings-api-key", "sk-remembered")
    set_value(user, "settings-remember-key", True)
    await _apply(user, page)

    set_value(user, "settings-provider", "none")
    await _apply(user, page)

    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "api_key" not in saved and saved["remember_api_key"] is False
    assert page.state.settings["api_key"] == ""
