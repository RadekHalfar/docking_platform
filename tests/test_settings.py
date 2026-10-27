"""Tests for ui/settings.py (persisted LLM provider settings)."""
import json
import os

import pytest

import ui.settings as settings_module
from ui.settings import build_llm_client, load_settings, save_settings


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    """Point SETTINGS_FILE at a tmp path and clear any relevant env vars."""
    settings_file = tmp_path / ".docking_platform_settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", settings_file)
    for var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_ENDPOINT", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    return settings_file


class TestLoadSettings:
    def test_defaults_when_no_file_and_no_env(self):
        settings = load_settings()
        # Phase 18: no LLM until the user picks one.
        assert settings["provider"] == "none"
        assert settings["model"] == ""
        assert settings["endpoint"] == "http://localhost:11434"
        assert settings["api_key"] == ""
        assert settings["base_url"] == ""

    def test_env_vars_override_defaults(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")

        settings = load_settings()

        assert settings["provider"] == "openai"
        assert settings["model"] == "gpt-4o-mini"
        assert settings["api_key"] == "sk-test"

    def test_saved_file_overrides_env(self, _isolated_settings_file, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        _isolated_settings_file.write_text(json.dumps({"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}))

        settings = load_settings()

        assert settings["provider"] == "anthropic"
        assert settings["model"] == "claude-haiku-4-5-20251001"

    def test_missing_file_falls_back_to_env(self, _isolated_settings_file):
        assert not _isolated_settings_file.exists()
        settings = load_settings()
        assert settings["provider"] == "none"

    def test_corrupt_file_falls_back_to_env(self, _isolated_settings_file):
        _isolated_settings_file.write_text("{not valid json")
        settings = load_settings()
        assert settings["provider"] == "none"


    def test_empty_provider_env_var_means_none(self, monkeypatch):
        # compose passes LLM_PROVIDER through even when .env leaves it empty
        monkeypatch.setenv("LLM_PROVIDER", "")
        assert load_settings()["provider"] == "none"


class TestBuildLlmClient:
    def test_none_provider_builds_no_client(self):
        assert build_llm_client(load_settings()) is None

    def test_a_real_provider_builds_one(self):
        settings = {"provider": "openai", "model": "gpt-4o-mini", "endpoint": "", "api_key": "sk-x", "base_url": ""}
        assert build_llm_client(settings) is not None


class TestSaveSettings:
    def test_save_then_load_round_trips(self, _isolated_settings_file):
        save_settings({
            "provider": "openai_compatible",
            "model": "local-model",
            "endpoint": "http://localhost:1234/v1",
            "base_url": "http://localhost:1234/v1",
            "api_key": "should-not-be-saved",
        })

        loaded = load_settings()
        assert loaded["provider"] == "openai_compatible"
        assert loaded["model"] == "local-model"
        assert loaded["base_url"] == "http://localhost:1234/v1"

    def test_api_key_is_not_persisted_unless_remembered(self, _isolated_settings_file):
        save_settings({
            "provider": "openai",
            "model": "gpt-4o-mini",
            "endpoint": "",
            "base_url": "",
            "api_key": "sk-super-secret",
        })

        on_disk = json.loads(_isolated_settings_file.read_text())
        assert "api_key" not in on_disk
        assert on_disk["remember_api_key"] is False

    def test_a_remembered_key_is_saved_and_overrides_the_env_var(self, _isolated_settings_file, monkeypatch):
        save_settings({
            "provider": "openai", "model": "gpt-4o-mini", "endpoint": "", "base_url": "",
            "api_key": "sk-remembered", "remember_api_key": True,
        })
        monkeypatch.setenv("LLM_API_KEY", "sk-from-env")

        loaded = load_settings()
        assert loaded["api_key"] == "sk-remembered" and loaded["remember_api_key"] is True

    def test_a_key_left_in_the_file_without_the_flag_is_ignored(self, _isolated_settings_file, monkeypatch):
        _isolated_settings_file.write_text(json.dumps({"provider": "openai", "api_key": "sk-stale"}))
        monkeypatch.setenv("LLM_API_KEY", "sk-from-env")

        loaded = load_settings()
        assert loaded["api_key"] == "sk-from-env" and loaded["remember_api_key"] is False

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_the_file_is_readable_only_by_its_owner(self, _isolated_settings_file):
        save_settings({"provider": "openai", "model": "m", "api_key": "sk", "remember_api_key": True})
        assert _isolated_settings_file.stat().st_mode & 0o777 == 0o600

    def test_creates_parent_directory_if_missing(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "dir" / "settings.json"
        monkeypatch.setattr(settings_module, "SETTINGS_FILE", nested)

        save_settings({"provider": "ollama", "model": "llama3", "endpoint": "", "base_url": ""})

        assert nested.exists()

    def test_replaces_the_file_rather_than_rewriting_it(self, _isolated_settings_file):
        # Phase 16, Stage 16.3: a new file swapped in needs write permission
        # on the directory only, so the non-root GUI can update a file the
        # former root Streamlit GUI created. Also: never a half-written file.
        _isolated_settings_file.write_text("{}")
        old_inode = _isolated_settings_file.stat().st_ino

        save_settings({"provider": "ollama", "model": "llama3", "endpoint": "", "base_url": ""})

        assert _isolated_settings_file.stat().st_ino != old_inode
        assert json.loads(_isolated_settings_file.read_text())["model"] == "llama3"
        assert list(_isolated_settings_file.parent.glob("*.tmp")) == []
