"""
Tests for LLM adapters and provider factory.
"""
import os
import pytest
from unittest.mock import patch

from services.llm_orchestrator import create_llm_client
from services.llm_orchestrator.llm_adapters import OllamaAdapter


class TestOllamaAdapter:
    """Adapter-level tests for Ollama provider."""

    def test_init(self):
        adapter = OllamaAdapter(endpoint="http://test:11434", model="llama2")
        assert adapter.endpoint == "http://test:11434"
        assert adapter.model == "llama2"
        assert adapter.generate_url == "http://test:11434/api/generate"

    @patch('services.llm_orchestrator.llm_adapters.requests.get')
    def test_check_health_success(self, mock_get):
        mock_get.return_value.status_code = 200
        adapter = OllamaAdapter()
        assert adapter.check_health() is True

    @patch('services.llm_orchestrator.llm_adapters.requests.get')
    def test_check_health_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection failed")
        adapter = OllamaAdapter()
        assert adapter.check_health() is False

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_generate(self, mock_post):
        mock_post.return_value.json.return_value = {"response": "hello"}
        mock_post.return_value.raise_for_status.return_value = None

        adapter = OllamaAdapter(model="llama2")
        response = adapter.generate("prompt")

        assert response == "hello"
        mock_post.assert_called_once()

    @patch('services.llm_orchestrator.llm_adapters.requests.get')
    def test_list_models(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "models": [
                {"name": "llama2"},
                {"name": "mistral"}
            ]
        }

        adapter = OllamaAdapter()
        models = adapter.list_models()

        assert models == ["llama2", "mistral"]


class TestLLMFactory:
    """Tests for provider factory behavior."""

    def test_create_ollama_client(self):
        client = create_llm_client("ollama", "http://test:11434", "llama2")
        assert isinstance(client, OllamaAdapter)


@pytest.mark.integration
def test_llm_env_wiring_health_smoke():
    """Non-mocked smoke test for provider/endpoint wiring via environment."""
    provider = os.getenv("LLM_PROVIDER")
    endpoint = os.getenv("LLM_ENDPOINT")
    model = os.getenv("LLM_MODEL", "llama2")

    if not provider or not endpoint:
        pytest.skip("LLM_PROVIDER/LLM_ENDPOINT not set; skipping runtime wiring smoke test")

    client = create_llm_client(provider=provider, endpoint=endpoint, model=model)
    assert client.check_health() is True
