"""
LLM factory.

Provides a single place to instantiate provider-specific adapters.
"""
from typing import Optional

from .llm_adapters import OllamaAdapter
from .llm_base import BaseLLMClient


def create_llm_client(provider: str, endpoint: str, model: str, api_key: Optional[str] = None) -> BaseLLMClient:
    """Create an LLM client for the selected provider."""
    normalized = provider.strip().lower()

    if normalized == "ollama":
        return OllamaAdapter(endpoint=endpoint, model=model, api_key=api_key)

    raise ValueError(f"Unsupported LLM provider: {provider}")
