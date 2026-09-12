"""
LLM factory.

Provides a single place to instantiate provider-specific adapters.
"""
from typing import Optional

from .llm_adapters import (
    AnthropicAdapter,
    MetaCentrumAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
)
from .llm_base import BaseLLMClient

KNOWN_PROVIDERS = ("ollama", "openai", "anthropic", "openai_compatible", "metacentrum")


def create_llm_client(
    provider: str,
    endpoint: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> BaseLLMClient:
    """Create an LLM client for the selected provider.

    Args:
        provider: One of 'ollama', 'openai', 'anthropic', 'openai_compatible',
                  'metacentrum'.
        endpoint: Ollama endpoint URL (used only for the 'ollama' provider).
        model: Model name to use.
        api_key: API key for cloud providers (OpenAI, Anthropic, MetaCentrum)
                 or optional auth for openai_compatible servers.
        base_url: Base URL for 'openai_compatible' servers (e.g.
                  http://localhost:1234/v1 for LM Studio). Falls back to
                  endpoint when not set. For 'metacentrum', overrides the
                  default https://llm.ai.e-infra.cz/v1 gateway URL.
    """
    normalized = provider.strip().lower()

    if normalized == "ollama":
        return OllamaAdapter(endpoint=endpoint, model=model, api_key=api_key)

    if normalized == "openai":
        return OpenAIAdapter(api_key=api_key or "", model=model)

    if normalized == "anthropic":
        return AnthropicAdapter(api_key=api_key or "", model=model)

    if normalized == "openai_compatible":
        return OpenAICompatibleAdapter(
            base_url=base_url or endpoint,
            model=model,
            api_key=api_key or "",
        )

    if normalized == "metacentrum":
        return MetaCentrumAdapter(
            api_key=api_key or "",
            model=model,
            base_url=base_url or None,
        )

    raise ValueError(
        f"Unsupported LLM provider: {provider!r}. Choose from: {', '.join(KNOWN_PROVIDERS)}"
    )
