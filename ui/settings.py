"""Persisted LLM provider settings for the GUI (no GUI framework dependency).

Settings survive a browser refresh (which resets the GUI's per-tab state) by
being written to a small JSON file on disk. The API key is written to that
file only when the user ticks "Remember API key" (Stage 18.3); otherwise it
comes from the LLM_API_KEY env var or is re-entered in the UI for the
current session only. The file is always written with mode 0600
(:data:`SETTINGS_FILE_MODE`), because it may hold that key in plain text.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from common.file_io import atomic_write_json, load_json_dict

# Overridable so a container deployment can point this at a mounted volume
# (see docker-compose.yml) and have settings survive container recreation,
# not just a browser refresh.
SETTINGS_FILE = Path(
    os.getenv("DOCKING_PLATFORM_SETTINGS_FILE", str(Path.home() / ".docking_platform_settings.json"))
)
# Readable only by the GUI's own user: the file may hold the API key.
SETTINGS_FILE_MODE = 0o600

# The Ollama endpoint when neither LLM_ENDPOINT nor a saved setting gives one.
DEFAULT_ENDPOINT = "http://localhost:11434"

# Always persisted to disk; api_key only when remember_api_key is set.
_PERSISTABLE_KEYS = ("provider", "model", "endpoint", "base_url")


def load_settings() -> Dict[str, Any]:
    """Return current LLM settings.

    Starts from environment variables (set by docker-compose.yml / .env),
    then overrides with any previously saved user preferences on disk,
    including a remembered API key.
    """
    from services.llm_orchestrator.llm_factory import NO_LLM_PROVIDER

    settings = {
        # Spelled as the factory reads it, so the dialog shows the provider
        # the client uses ("OpenAI" in .env is openai, Review Stage R5).
        "provider": (os.getenv("LLM_PROVIDER") or "").strip().lower() or NO_LLM_PROVIDER,
        "model": os.getenv("LLM_MODEL", ""),
        "endpoint": os.getenv("LLM_ENDPOINT", DEFAULT_ENDPOINT),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", ""),
        "remember_api_key": False,
    }

    # A missing, unreadable or non-object file counts as nothing saved.
    saved = load_json_dict(SETTINGS_FILE) or {}
    for key in _PERSISTABLE_KEYS:
        if saved.get(key):
            settings[key] = saved[key]
    if saved.get("remember_api_key") is True:
        settings["remember_api_key"] = True
        if saved.get("api_key"):
            settings["api_key"] = saved["api_key"]

    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist provider/model/endpoint/base_url to disk, and the API key only
    when ``remember_api_key`` is set -- unticking it removes a saved key."""
    to_save: Dict[str, Any] = {key: settings[key] for key in _PERSISTABLE_KEYS if key in settings}
    remember = bool(settings.get("remember_api_key"))
    to_save["remember_api_key"] = remember
    if remember and settings.get("api_key"):
        to_save["api_key"] = settings["api_key"]
    # Replaced, not rewritten in place: no half-written file, and the
    # non-root GUI can update a file the former root Streamlit GUI created.
    atomic_write_json(SETTINGS_FILE, to_save, mode=SETTINGS_FILE_MODE)


def build_llm_client(settings: Dict[str, Any]):
    """The LLM client for *settings* (as :func:`load_settings` returns them),
    or None for the ``none`` provider. Building it makes no network call."""
    from services.llm_orchestrator import create_llm_client, is_llm_configured

    if not is_llm_configured(settings["provider"]):
        return None
    return create_llm_client(
        provider=settings["provider"],
        endpoint=settings["endpoint"],
        model=settings["model"],
        api_key=settings.get("api_key"),
        base_url=settings.get("base_url"),
    )
