"""Persisted LLM provider settings for the GUI (no GUI framework dependency).

Settings survive a browser refresh (which resets the GUI's per-tab state) by
being written to a small JSON file on disk. API keys are never written to that
file — they always come from the LLM_API_KEY env var or are re-entered in the
UI for the current session only.
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

# The Ollama endpoint when neither LLM_ENDPOINT nor a saved setting gives one.
DEFAULT_ENDPOINT = "http://localhost:11434"

# Only these keys are persisted to disk.
_PERSISTABLE_KEYS = ("provider", "model", "endpoint", "base_url")


def load_settings() -> Dict[str, Any]:
    """Return current LLM settings.

    Starts from environment variables (set by docker-compose.yml / .env),
    then overrides with any previously saved user preferences on disk.
    """
    settings = {
        "provider": os.getenv("LLM_PROVIDER", "ollama"),
        "model": os.getenv("LLM_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"),
        "endpoint": os.getenv("LLM_ENDPOINT", DEFAULT_ENDPOINT),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", ""),
    }

    # A missing, unreadable or non-object file counts as nothing saved.
    saved = load_json_dict(SETTINGS_FILE) or {}
    for key in _PERSISTABLE_KEYS:
        if saved.get(key):
            settings[key] = saved[key]

    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist provider/model/endpoint/base_url to disk. Never writes api_key."""
    to_save = {key: settings[key] for key in _PERSISTABLE_KEYS if key in settings}
    # Replaced, not rewritten in place: no half-written file, and the
    # non-root GUI can update a file the former root Streamlit GUI created.
    atomic_write_json(SETTINGS_FILE, to_save)


def build_llm_client(settings: Dict[str, Any]):
    """The LLM client for *settings* (as :func:`load_settings` returns them).
    Building it makes no network call."""
    from services.llm_orchestrator import create_llm_client

    return create_llm_client(
        provider=settings["provider"],
        endpoint=settings["endpoint"],
        model=settings["model"],
        api_key=settings.get("api_key"),
        base_url=settings.get("base_url"),
    )
