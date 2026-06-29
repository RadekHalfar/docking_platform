"""
LLM base interface.

Defines a provider-agnostic contract for all LLM adapters.
"""
from abc import ABC, abstractmethod
from typing import Optional


class BaseLLMClient(ABC):
    """Abstract base class for provider-specific LLM clients."""

    def __init__(self, endpoint: str, model: str, api_key: Optional[str] = None):
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        """Generate text from the model."""

    @abstractmethod
    def check_health(self) -> bool:
        """Check if the provider endpoint is healthy."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models for the provider."""
