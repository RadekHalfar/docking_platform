"""Persisted LLM provider settings for the Streamlit UI (no Streamlit dependency).

Settings survive a browser refresh (which resets Streamlit's session_state) by
being written to a small JSON file on disk. API keys are never written to that
file — they always come from the LLM_API_KEY env var or are re-entered in the
UI for the current session only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from common.file_io import atomic_write_json

# Overridable so a container deployment can point this at a mounted volume
# (see docker-compose.yml) and have settings survive container recreation,
# not just a browser refresh.
SETTINGS_FILE = Path(
    os.getenv("DOCKING_PLATFORM_SETTINGS_FILE", str(Path.home() / ".docking_platform_settings.json"))
)

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
        "endpoint": os.getenv("LLM_ENDPOINT", "http://localhost:11434"),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", ""),
    }

    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            saved = {}
        for key in _PERSISTABLE_KEYS:
            if saved.get(key):
                settings[key] = saved[key]

    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist provider/model/endpoint/base_url to disk. Never writes api_key."""
    to_save = {key: settings[key] for key in _PERSISTABLE_KEYS if key in settings}
    # Replaced, not rewritten in place: no half-written file, and the
    # non-root NiceGUI UI can update a file the root Streamlit UI created.
    atomic_write_json(SETTINGS_FILE, to_save)
