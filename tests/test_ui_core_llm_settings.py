"""Tests for ui/core/llm_settings.py (Stage 16.7) -- hermetic, plain GuiSession."""
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


@pytest.mark.parametrize("provider, base_url", [("openai_compatible", "http://x/v1"), ("ollama", ""), ("metacentrum", "")])
def test_settings_from_form_strips_and_keeps_base_url_only_where_it_has_a_field(provider, base_url):
    settings = llm_settings.settings_from_form(provider, " m ", " http://e ", " k ", " http://x/v1 ")

    assert settings == {"provider": provider, "model": "m", "endpoint": "http://e", "api_key": "k",
                        "base_url": base_url}


def test_apply_saves_all_but_the_key_and_rebuilds_the_client(state, settings_file):
    new = llm_settings.settings_from_form("openai", "gpt-x", "", "sk-test", "")

    llm_settings.use_settings(state, new, llm_settings.persist_settings(new))

    assert state.settings == new
    assert state.llm_client is not None and state.llm_client != "old"
    saved = json.loads(settings_file.read_text())
    assert saved["provider"] == "openai" and saved["model"] == "gpt-x"
    assert "api_key" not in saved


def test_settings_the_client_rejects_change_nothing(state, settings_file, monkeypatch):
    # Saved first, a rejected provider used to stay on disk and in the session.
    def reject(settings):
        raise ValueError("Unsupported provider")

    monkeypatch.setattr(llm_settings, "build_llm_client", reject)

    with pytest.raises(ValueError):
        new = {"provider": "nope", "model": "", "endpoint": "", "api_key": "", "base_url": ""}
        llm_settings.use_settings(state, new, llm_settings.persist_settings(new))

    assert state.settings == {"provider": "ollama"} and state.llm_client == "old"
    assert not settings_file.exists()
