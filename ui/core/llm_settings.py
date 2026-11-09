"""The LLM settings form's logic.

Loading and saving the settings file is ``ui.settings``' job.
"""
from __future__ import annotations

from services.llm_orchestrator import is_llm_configured
from ui.settings import DEFAULT_ENDPOINT, build_llm_client, save_settings

PROVIDER_LABELS = {
    "none": "None (forms only)",
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "metacentrum": "MetaCentrum (e-INFRA CZ)",
    "openai_compatible": "Other local server",
}
# The providers that take an API key field.
API_KEY_PROVIDERS = ("openai", "anthropic", "metacentrum", "openai_compatible")
# Apply's refusal of a provider without a model: LLM_MODEL defaults to
# empty, so nothing else supplies one.
MODEL_REQUIRED = "Pick or type a model for {provider}."

# The Ollama models the settings dialog offers to download, as
# (name, download size, note). The same two README's "Choose an LLM" table
# recommends: gemma3:4b is the light choice, and the model the platform's
# live checks parse workflows with; the Qwen 2.5 Coder 7B Q4
# build has stronger instruction following but wants 16 GB RAM. Any other
# Ollama library name can be typed in the dialog.
RECOMMENDED_OLLAMA_MODELS = (
    ("gemma3:4b", "3.3 GB", "lighter; fine for 8–16 GB RAM"),
    ("qwen2.5-coder:7b-instruct-q4_K_M", "4.7 GB", "strong instruction following; 16 GB RAM"),
)


def download_options(downloaded) -> dict:
    """The recommended Ollama models not in *downloaded*, as {name: label}."""
    return {
        name: f"{name} — {size}, {note}"
        for name, size, note in RECOMMENDED_OLLAMA_MODELS
        if name not in downloaded
    }


def settings_from_form(
    provider: str, model: str, endpoint: str, api_key: str, base_url: str, remember_api_key: bool = False,
) -> dict:
    """The settings the form's fields describe, stripped.

    A hidden field's value is dropped instead of silently carried into the
    new client (or the settings file):
    - only ``openai_compatible`` has a Base URL field. For every other
      provider (including metacentrum, which has no override field yet) a
      stale value left over from an earlier openai_compatible session goes;
    - only :data:`API_KEY_PROVIDERS` have the API key and "Remember" fields,
      so for the others a remembered key is not saved again.
    """
    takes_key = provider in API_KEY_PROVIDERS
    return {
        "provider": provider,
        "model": model.strip(),
        "endpoint": endpoint.strip(),
        "api_key": api_key.strip() if takes_key else "",
        "base_url": base_url.strip() if provider == "openai_compatible" else "",
        "remember_api_key": bool(remember_api_key) and takes_key,
    }


def persist_settings(new_settings: dict):
    """Build the LLM client for *new_settings*, then save them; returns the
    client. Built first, so settings it rejects are not saved -- as is a
    provider without a model (:data:`MODEL_REQUIRED`, ValueError). The API
    key is written to disk only when ``remember_api_key`` is set
    (``ui.settings.save_settings``)."""
    provider = new_settings["provider"]
    if is_llm_configured(provider) and not new_settings["model"]:
        raise ValueError(MODEL_REQUIRED.format(provider=PROVIDER_LABELS.get(provider, provider)))
    client = build_llm_client(new_settings)
    save_settings(new_settings)
    return client


def use_settings(state, new_settings: dict, client) -> None:
    """Make *new_settings* and their client this session's. The API key
    stays in the session state for immediate use."""
    state.settings = new_settings
    state.llm_client = client


