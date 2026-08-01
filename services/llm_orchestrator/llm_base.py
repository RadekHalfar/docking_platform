"""
LLM base interface.

Defines a provider-agnostic contract for all LLM adapters.
"""
from abc import ABC, abstractmethod
from typing import Optional

from common.http_retry import build_llm_retry_session


class BaseLLMClient(ABC):
    """Abstract base class for provider-specific LLM clients."""

    def __init__(self, endpoint: str, model: str, api_key: Optional[str] = None):
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        # Retries connection failures/read timeouts/transient 5xx with
        # backoff -- see common/http_retry.py for why LLM calls can afford
        # a more liberal retry policy than MCP tool calls.
        self._session = build_llm_retry_session()

    @abstractmethod
    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        """Generate text from the model."""

    @abstractmethod
    def check_health(self) -> bool:
        """Check if the provider endpoint is healthy."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models for the provider."""
