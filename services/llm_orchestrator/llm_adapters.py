"""
LLM adapters.

Contains provider-specific implementations of the shared LLM interface.
"""
import logging
import json
from typing import Optional

import requests

from .llm_base import BaseLLMClient

logger = logging.getLogger(__name__)


class OllamaAdapter(BaseLLMClient):
    """LLM adapter for Ollama API."""

    def __init__(self, endpoint: str = "http://localhost:11434", model: str = "llama2", api_key: Optional[str] = None):
        super().__init__(endpoint=endpoint, model=model, api_key=api_key)
        self.generate_url = f"{self.endpoint}/api/generate"
        self.tags_url = f"{self.endpoint}/api/tags"
        self.pull_url = f"{self.endpoint}/api/pull"

    def check_health(self) -> bool:
        """Check if Ollama endpoint is reachable."""
        try:
            response = requests.get(self.tags_url, timeout=5)
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Ollama health check failed: {e}")
            return False

    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        """Generate text using Ollama."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }

        if system:
            payload["system"] = system

        try:
            logger.info(f"Calling Ollama with model: {self.model}")
            response = requests.post(self.generate_url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            if response is not None and response.status_code == 404 and not self._model_available():
                logger.info(f"Model {self.model} not found in Ollama; pulling it now")
                self._pull_model()
                retry_response = requests.post(self.generate_url, json=payload, timeout=120)
                retry_response.raise_for_status()
                result = retry_response.json()
                return result.get("response", "")
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        except requests.RequestException as e:
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")

    def list_models(self) -> list:
        """List models available in Ollama."""
        try:
            response = requests.get(self.tags_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except requests.RequestException as e:
            logger.error(f"Failed to list models: {e}")
            return []

    def _model_available(self) -> bool:
        return self.model in self.list_models()

    def _pull_model(self) -> None:
        """Pull the configured model into the Ollama instance."""
        payload = {"name": self.model, "stream": True}
        try:
            response = requests.post(self.pull_url, json=payload, stream=True, timeout=600)
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                json.loads(line)
        except requests.RequestException as e:
            logger.error(f"Failed to pull Ollama model {self.model}: {e}")
            raise RuntimeError(f"Failed to pull Ollama model {self.model}: {e}")
