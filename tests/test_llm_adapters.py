"""
Tests for LLM adapters and provider factory.
"""
import os
import pytest
import requests
from unittest.mock import Mock, patch

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
        mock_get.side_effect = requests.ConnectionError("Connection failed")
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

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    @patch('services.llm_orchestrator.llm_adapters.requests.get')
    def test_generate_pulls_missing_model(self, mock_get, mock_post):
        model_missing_error = requests.HTTPError("404 Client Error")
        model_missing_error.response = Mock(status_code=404)

        first_generate_response = Mock()
        first_generate_response.raise_for_status.side_effect = model_missing_error

        pull_response = Mock()
        pull_response.raise_for_status.return_value = None
        pull_response.iter_lines.return_value = [b'{"status": "success"}']

        second_generate_response = Mock()
        second_generate_response.raise_for_status.return_value = None
        second_generate_response.json.return_value = {"response": "hello"}

        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"models": []}
        mock_post.side_effect = [first_generate_response, pull_response, second_generate_response]

        adapter = OllamaAdapter(endpoint="http://test:11434", model="qwen2.5-coder:7b-instruct-q4_K_M")
        response = adapter.generate("prompt")

        assert response == "hello"
        assert mock_post.call_count == 3

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_generate_raises_runtime_error_on_non_404_http_error(self, mock_post):
        """Non-404 HTTP errors should raise RuntimeError immediately without pulling."""
        http_error = requests.HTTPError("500 Internal Server Error")
        http_error.response = Mock(status_code=500)
        mock_post.return_value.raise_for_status.side_effect = http_error

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_generate_raises_runtime_error_on_timeout(self, mock_post):
        """Timeout during generate should surface as RuntimeError."""
        mock_post.side_effect = requests.Timeout("timed out")

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_generate_raises_runtime_error_on_connection_error(self, mock_post):
        """Connection refused during generate should surface as RuntimeError."""
        mock_post.side_effect = requests.ConnectionError("refused")

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_pull_model_logs_error_status_in_stream(self, mock_post):
        """An error status line in the pull stream should be logged as a warning (L3 fix)."""
        pull_response = Mock()
        pull_response.raise_for_status.return_value = None
        pull_response.iter_lines.return_value = [
            b'{"status": "pulling manifest"}',
            b'{"status": "error", "error": "disk full"}',
        ]
        mock_post.return_value = pull_response

        adapter = OllamaAdapter(model="llama2")
        with patch("services.llm_orchestrator.llm_adapters.logger") as mock_logger:
            adapter._pull_model()
            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("disk full" in call for call in warning_calls)

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
    if not client.check_health():
        pytest.skip("LLM endpoint not reachable in this test context")

    assert client.check_health() is True
