"""Discover locally available Ollama models (no GUI framework dependency)."""
from __future__ import annotations

from typing import List


def list_ollama_models(endpoint: str, timeout: float = 3.0) -> List[str]:
    """Return sorted model names available at an Ollama endpoint's /api/tags.

    Returns an empty list if the endpoint is empty, unreachable, or returns no
    models — callers should fall back to a free-text input in that case.
    """
    if not endpoint:
        return []

    import requests

    try:
        resp = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    try:
        data = resp.json()
    except ValueError:
        return []

    names = {m.get("name") for m in data.get("models", []) if m.get("name")}
    return sorted(names)
