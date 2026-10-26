"""The LLM settings form's logic (Phase 16, Stage 16.7).

Loading and saving the settings file is ``ui.settings``' job.
"""
from __future__ import annotations

from ui.settings import DEFAULT_ENDPOINT, build_llm_client, save_settings

PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "metacentrum": "MetaCentrum (e-INFRA CZ)",
    "openai_compatible": "Other local server",
}
# The providers that take an API key field.
API_KEY_PROVIDERS = ("openai", "anthropic", "metacentrum", "openai_compatible")


def settings_from_form(provider: str, model: str, endpoint: str, api_key: str, base_url: str) -> dict:
    """The settings the form's fields describe, stripped.

    Only ``openai_compatible`` has a Base URL field. For every other
    provider (including metacentrum, which has no override field yet) a
    stale value left over from an earlier openai_compatible session is
    dropped instead of silently carried into the new client.
    """
    return {
        "provider": provider,
        "model": model.strip(),
        "endpoint": endpoint.strip(),
        "api_key": api_key.strip(),
        "base_url": base_url.strip() if provider == "openai_compatible" else "",
    }


def persist_settings(new_settings: dict):
    """Build the LLM client for *new_settings*, then save them; returns the
    client. Built first, so settings it rejects are not saved. The API key
    is never written to disk (``ui.settings.save_settings``)."""
    client = build_llm_client(new_settings)
    save_settings(new_settings)
    return client


def use_settings(state, new_settings: dict, client) -> None:
    """Make *new_settings* and their client this session's. The API key
    stays in the session state for immediate use."""
    state.settings = new_settings
    state.llm_client = client


