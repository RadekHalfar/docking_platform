"""
LLM adapters.

Contains provider-specific implementations of the shared LLM interface.
"""
import logging
import json
from typing import Optional

import requests

from .llm_base import DEFAULT_LLM_TIMEOUT_SECONDS, BaseLLMClient

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
            response = self._session.get(self.tags_url, timeout=5)
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
            response = self._session.post(self.generate_url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            if response is not None and response.status_code == 404 and not self._model_available():
                logger.info(f"Model {self.model} not found in Ollama; pulling it now")
                self._pull_model()
                retry_response = self._session.post(self.generate_url, json=payload, timeout=120)
                retry_response.raise_for_status()
                result = retry_response.json()
                return result.get("response", "")
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        except requests.RequestException as e:
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")

    def list_models(self) -> list[str]:
        """List models available in Ollama."""
        try:
            response = self._session.get(self.tags_url, timeout=10)
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
            response = self._session.post(self.pull_url, json=payload, stream=True, timeout=600)
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("status") == "error":
                        logger.warning("Ollama pull warning: %s", data.get("error", line))
                except json.JSONDecodeError:
                    pass
        except requests.RequestException as e:
            logger.error(f"Failed to pull Ollama model {self.model}: {e}")
            raise RuntimeError(f"Failed to pull Ollama model {self.model}: {e}")


class OpenAIAdapter(BaseLLMClient):
    """LLM adapter for the OpenAI API (https://api.openai.com/v1)."""

    _BASE = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
        super().__init__(endpoint=self._BASE, model=model, api_key=api_key, timeout=timeout)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        try:
            response = self._session.post(
                f"{self.endpoint}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.HTTPError as e:
            logger.error(f"OpenAI API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        except requests.RequestException as e:
            logger.error(f"OpenAI connection error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")

    def check_health(self) -> bool:
        try:
            response = self._session.get(f"{self.endpoint}/models", headers=self._headers(), timeout=10)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list:
        try:
            response = self._session.get(f"{self.endpoint}/models", headers=self._headers(), timeout=10)
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"Failed to list OpenAI models: {e}")
            return []


class AnthropicAdapter(BaseLLMClient):
    """LLM adapter for the Anthropic API (https://api.anthropic.com)."""

    _BASE = "https://api.anthropic.com"
    _API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    ):
        super().__init__(endpoint=self._BASE, model=model, api_key=api_key, timeout=timeout)

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self._API_VERSION,
            "Content-Type": "application/json",
        }

    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        payload: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        try:
            response = self._session.post(
                f"{self.endpoint}/v1/messages",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["content"][0]["text"]
        except requests.HTTPError as e:
            logger.error(f"Anthropic API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        except requests.RequestException as e:
            logger.error(f"Anthropic connection error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")

    def check_health(self) -> bool:
        try:
            response = self._session.get(
                f"{self.endpoint}/v1/models", headers=self._headers(), timeout=10
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list:
        try:
            response = self._session.get(
                f"{self.endpoint}/v1/models", headers=self._headers(), timeout=10
            )
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"Failed to list Anthropic models: {e}")
            return []


class OpenAICompatibleAdapter(BaseLLMClient):
    """LLM adapter for any server that speaks the OpenAI chat-completions API.

    Works with LM Studio, vLLM, llama.cpp server, Jan, Groq, Together AI, etc.
    Set base_url to the server's base URL including /v1 where applicable.
    api_key is optional — pass an empty string or a placeholder for servers that
    don't require authentication.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    ):
        super().__init__(endpoint=base_url.rstrip("/"), model=model, api_key=api_key, timeout=timeout)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        try:
            response = self._session.post(
                f"{self.endpoint}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.HTTPError as e:
            logger.error(f"OpenAI-compatible API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        except requests.RequestException as e:
            logger.error(f"OpenAI-compatible connection error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")

    def check_health(self) -> bool:
        try:
            response = self._session.get(
                f"{self.endpoint}/models", headers=self._headers(), timeout=10
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list:
        try:
            response = self._session.get(
                f"{self.endpoint}/models", headers=self._headers(), timeout=10
            )
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"Failed to list models from {self.endpoint}: {e}")
            return []


class MetaCentrumAdapter(OpenAICompatibleAdapter):
    """LLM adapter for MetaCentrum's e-INFRA CZ hosted LLM gateway.

    The gateway (https://llm.ai.e-infra.cz/v1) speaks the same OpenAI
    chat-completions schema as OpenAICompatibleAdapter, so this class adds
    nothing but MetaCentrum-friendly defaults on top of it: a default
    base_url and a longer default timeout (cloud-hosted large models are
    slower than a local LM Studio/vLLM instance).

    Known caveat (verified against the real gateway): non-streaming requests
    have a hard, non-overridable ~30-minute server-side timeout. This
    project only ever sends short parameter-extraction prompts, so a shorter
    client-side timeout is used instead of implementing SSE streaming --
    increase `timeout` (or add a streaming path) if a future caller ever
    needs to generate long-form text against MetaCentrum.
    """

    _DEFAULT_BASE_URL = "https://llm.ai.e-infra.cz/v1"
    _DEFAULT_TIMEOUT_SECONDS = 300.0

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ):
        super().__init__(
            base_url=base_url or self._DEFAULT_BASE_URL,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
