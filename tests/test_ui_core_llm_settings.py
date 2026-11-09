"""Tests for ui/core/llm_settings.py -- hermetic, plain GuiSession."""
import json

import pytest

import ui.settings as settings_module
from ui.core import llm_settings
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", path)
    return path


@pytest.fixture
def state():
    return GuiSession(worker_cap=WorkerCap(10, "compose"), settings={"provider": "ollama"}, llm_client="old")


@pytest.mark.parametrize("provider, base_url, api_key", [
    ("openai_compatible", "http://x/v1", "k"), ("ollama", "", ""), ("metacentrum", "", "k"),
])
def test_settings_from_form_strips_and_keeps_only_the_fields_it_shows(provider, base_url, api_key):
    settings = llm_settings.settings_from_form(provider, " m ", " http://e ", " k ", " http://x/v1 ")

    assert settings == {"provider": provider, "model": "m", "endpoint": "http://e", "api_key": api_key,
                        "base_url": base_url, "remember_api_key": False}


@pytest.mark.parametrize("provider", ["ollama", "none"])
def test_a_provider_without_a_key_field_neither_keeps_nor_remembers_one(provider):
    # Its key fields are only hidden; a remembered key must not be saved
    # again with no way to untick it.
    settings = llm_settings.settings_from_form(provider, "m", "", "sk-old", "", remember_api_key=True)

    assert settings["api_key"] == "" and settings["remember_api_key"] is False


@pytest.mark.parametrize("provider", ["openai", "ollama", "openai_compatible"])
def test_a_provider_without_a_model_is_refused_and_not_saved(state, settings_file, provider):
    # LLM_MODEL is empty by default.
    new = llm_settings.settings_from_form(provider, " ", "http://e", "k", "http://x/v1")

    with pytest.raises(ValueError, match="Pick or type a model"):
        llm_settings.persist_settings(new)
    assert not settings_file.exists()


def test_apply_saves_all_but_the_key_and_rebuilds_the_client(state, settings_file):
    new = llm_settings.settings_from_form("openai", "gpt-x", "", "sk-test", "")

    llm_settings.use_settings(state, new, llm_settings.persist_settings(new))

    assert state.settings == new
    assert state.llm_client is not None and state.llm_client != "old"
    saved = json.loads(settings_file.read_text())
    assert saved["provider"] == "openai" and saved["model"] == "gpt-x"
    assert "api_key" not in saved


def test_settings_the_client_rejects_change_nothing(state, settings_file, monkeypatch):
    # Saved first, a rejected provider would stay on disk and in the session.
    def reject(settings):
        raise ValueError("Unsupported provider")

    monkeypatch.setattr(llm_settings, "build_llm_client", reject)

    with pytest.raises(ValueError):
        new = {"provider": "nope", "model": "m", "endpoint": "", "api_key": "", "base_url": ""}
        llm_settings.use_settings(state, new, llm_settings.persist_settings(new))

    assert state.settings == {"provider": "ollama"} and state.llm_client == "old"
    assert not settings_file.exists()


def test_applying_none_drops_the_client(state, settings_file):
    # "None (forms only)" is a provider like any other in the form;
    # it just has no client, so the chat answers with the setup hint.
    new = llm_settings.settings_from_form("none", "", "", "", "")

    llm_settings.use_settings(state, new, llm_settings.persist_settings(new))

    assert state.llm_client is None
    assert json.loads(settings_file.read_text())["provider"] == "none"


def test_none_is_offered_first():
    assert next(iter(llm_settings.PROVIDER_LABELS)) == "none"


def test_a_remembered_key_is_saved_and_unticking_removes_it(state, settings_file):
    new = llm_settings.settings_from_form("openai", "gpt-x", "", " sk-test ", "", remember_api_key=True)
    llm_settings.persist_settings(new)
    assert json.loads(settings_file.read_text())["api_key"] == "sk-test"

    llm_settings.persist_settings(llm_settings.settings_from_form("openai", "gpt-x", "", "sk-test", ""))
    assert "api_key" not in json.loads(settings_file.read_text())


def test_download_options_are_the_recommended_models_not_yet_downloaded():
    names = [name for name, _, _ in llm_settings.RECOMMENDED_OLLAMA_MODELS]
    options = llm_settings.download_options([names[0], "other:latest"])

    assert list(options) == names[1:]
    _, size, note = llm_settings.RECOMMENDED_OLLAMA_MODELS[1]
    assert options[names[1]] == f"{names[1]} — {size}, {note}"
