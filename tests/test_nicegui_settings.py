"""The NiceGUI settings dialog (Phase 16, Stage 16.7): the LLM provider,
endpoint, model and API key -- Streamlit's sidebar settings form, which
has no AppTest of its own. Runs in the ui-nice tier only (see
tests/test_nicegui_setup.py).
"""
import json

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
    """Wait until the dialog has listed the Ollama models (off the event loop)."""
    view = _view(page)
    await eventually(lambda: not view.fetching and view._models is not None)


async def _open_dialog(user, page):
    await user.open("/")
    click(user, "settings-button")
    await _models_listed(page)


async def _apply(user, page):
    click(user, "settings-apply")
    view = _view(page)
    await eventually(lambda: not view.applying and element(user, "settings-message").text)


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


async def test_unreachable_ollama_falls_back_to_free_text(user: User, page):
    # The harness's Ollama lists no models.
    await _open_dialog(user, page)

    assert element(user, "settings-model").visible
    assert not element(user, "settings-model-select").visible
    assert element(user, "settings-models-missing").visible


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
