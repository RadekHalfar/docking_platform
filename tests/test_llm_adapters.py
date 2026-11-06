"""
Tests for LLM adapters and provider factory.
"""
import os
import pytest
import requests
from unittest.mock import Mock, patch

from services.llm_orchestrator import create_llm_client
from services.llm_orchestrator.llm_adapters import (
    AnthropicAdapter,
    MetaCentrumAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    pull_ollama_model,
)
from services.llm_orchestrator.llm_factory import is_llm_configured


class TestOllamaAdapter:
    """Adapter-level tests for Ollama provider."""

    def test_init(self):
        adapter = OllamaAdapter(endpoint="http://test:11434", model="llama2")
        assert adapter.endpoint == "http://test:11434"
        assert adapter.model == "llama2"
        assert adapter.generate_url == "http://test:11434/api/generate"

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.get')
    def test_check_health_success(self, mock_get):
        mock_get.return_value.status_code = 200
        adapter = OllamaAdapter()
        assert adapter.check_health() is True

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.get')
    def test_check_health_failure(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("Connection failed")
        adapter = OllamaAdapter()
        assert adapter.check_health() is False

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    def test_generate(self, mock_post):
        mock_post.return_value.json.return_value = {"response": "hello"}
        mock_post.return_value.raise_for_status.return_value = None

        adapter = OllamaAdapter(model="llama2")
        response = adapter.generate("prompt")

        assert response == "hello"
        mock_post.assert_called_once()

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    @patch('services.llm_orchestrator.llm_adapters.requests.Session.get')
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

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    def test_generate_raises_runtime_error_on_non_404_http_error(self, mock_post):
        """Non-404 HTTP errors should raise RuntimeError immediately without pulling."""
        http_error = requests.HTTPError("500 Internal Server Error")
        http_error.response = Mock(status_code=500)
        mock_post.return_value.raise_for_status.side_effect = http_error

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    def test_generate_raises_runtime_error_on_timeout(self, mock_post):
        """Timeout during generate should surface as RuntimeError."""
        mock_post.side_effect = requests.Timeout("timed out")

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    def test_generate_raises_runtime_error_on_connection_error(self, mock_post):
        """Connection refused during generate should surface as RuntimeError."""
        mock_post.side_effect = requests.ConnectionError("refused")

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.post')
    def test_pull_model_raises_on_an_error_line_in_stream(self, mock_post):
        """Ollama reports a failed pull as an {"error": ...} line; it was only
        logged before Stage 18.3, so the retried generate failed with a 404."""
        pull_response = Mock()
        pull_response.iter_lines.return_value = [
            b'{"status": "pulling manifest"}',
            b'{"error": "disk full"}',
        ]
        mock_post.return_value = pull_response

        adapter = OllamaAdapter(model="llama2")
        with pytest.raises(RuntimeError, match="disk full"):
            adapter._pull_model()
        pull_response.close.assert_called_once()

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.get')
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

    @patch('services.llm_orchestrator.llm_adapters.requests.Session.get')
    def test_list_models_shares_the_listing_and_stays_a_list_when_unreachable(self, mock_get):
        # The GUI's listing and this one are one function since Review
        # Stage R5; the adapter keeps its [] for "can't be reached".
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"models": [{"name": "b"}, {"digest": "x"}, {"name": "a"}]}
        assert OllamaAdapter().list_models() == ["a", "b"]

        mock_get.side_effect = requests.ConnectionError("refused")
        assert OllamaAdapter().list_models() == []


class TestOpenAIAdapter:
    """Tests for OpenAI provider."""

    def _make_chat_response(self, content: str):
        mock = Mock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"choices": [{"message": {"content": content}}]}
        return mock

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_success(self, mock_post):
        mock_post.return_value = self._make_chat_response("hello from openai")
        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o-mini")
        assert adapter.generate("prompt") == "hello from openai"

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_sends_system_message(self, mock_post):
        mock_post.return_value = self._make_chat_response("ok")
        adapter = OpenAIAdapter(api_key="sk-test")
        adapter.generate("user prompt", system="system prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
        assert payload["messages"][1] == {"role": "user", "content": "user prompt"}

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_raises_on_http_error(self, mock_post):
        err = requests.HTTPError("401 Unauthorized")
        err.response = Mock(status_code=401)
        mock_post.return_value.raise_for_status.side_effect = err
        adapter = OpenAIAdapter(api_key="sk-bad")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_success(self, mock_get):
        mock_get.return_value.status_code = 200
        assert OpenAIAdapter(api_key="sk-test").check_health() is True

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_failure(self, mock_get):
        mock_get.side_effect = requests.ConnectionError()
        assert OpenAIAdapter(api_key="sk-test").check_health() is False

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_list_models(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]
        }
        models = OpenAIAdapter(api_key="sk-test").list_models()
        assert models == ["gpt-4o", "gpt-4o-mini"]

    def test_authorization_header_set(self):
        adapter = OpenAIAdapter(api_key="sk-mykey")
        assert adapter._headers()["Authorization"] == "Bearer sk-mykey"


class TestAnthropicAdapter:
    """Tests for Anthropic provider."""

    def _make_messages_response(self, text: str):
        mock = Mock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"content": [{"text": text}]}
        return mock

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_success(self, mock_post):
        mock_post.return_value = self._make_messages_response("hello from claude")
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        assert adapter.generate("prompt") == "hello from claude"

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_includes_system_field(self, mock_post):
        mock_post.return_value = self._make_messages_response("ok")
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        adapter.generate("user prompt", system="system prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["system"] == "system prompt"
        assert payload["messages"] == [{"role": "user", "content": "user prompt"}]

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_raises_on_auth_error(self, mock_post):
        err = requests.HTTPError("401 Unauthorized")
        err.response = Mock(status_code=401)
        mock_post.return_value.raise_for_status.side_effect = err
        adapter = AnthropicAdapter(api_key="sk-ant-bad")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_uses_models_endpoint(self, mock_get):
        mock_get.return_value.status_code = 200
        AnthropicAdapter(api_key="sk-ant-test").check_health()
        assert "/v1/models" in mock_get.call_args.args[0]

    def test_correct_headers_set(self):
        adapter = AnthropicAdapter(api_key="sk-ant-mykey")
        headers = adapter._headers()
        assert headers["x-api-key"] == "sk-ant-mykey"
        assert headers["anthropic-version"] == "2023-06-01"


class TestOpenAICompatibleAdapter:
    """Tests for OpenAI-compatible provider (LM Studio, vLLM, etc.)."""

    def _make_chat_response(self, content: str):
        mock = Mock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"choices": [{"message": {"content": content}}]}
        return mock

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_uses_custom_base_url(self, mock_post):
        mock_post.return_value = self._make_chat_response("ok")
        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:1234/v1", model="local-model"
        )
        adapter.generate("prompt")
        assert "http://localhost:1234/v1/chat/completions" in mock_post.call_args.args[0]

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_works_without_api_key(self, mock_post):
        mock_post.return_value = self._make_chat_response("ok")
        adapter = OpenAICompatibleAdapter(base_url="http://localhost:1234/v1", model="m")
        adapter.generate("prompt")
        headers = mock_post.call_args.kwargs["headers"]
        assert "Authorization" not in headers

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_sends_auth_when_api_key_provided(self, mock_post):
        mock_post.return_value = self._make_chat_response("ok")
        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:1234/v1", model="m", api_key="my-key"
        )
        adapter.generate("prompt")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer my-key"

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_hits_models_endpoint(self, mock_get):
        mock_get.return_value.status_code = 200
        adapter = OpenAICompatibleAdapter(base_url="http://localhost:8000/v1", model="m")
        assert adapter.check_health() is True
        assert "http://localhost:8000/v1/models" in mock_get.call_args.args[0]

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_raises_on_http_error(self, mock_post):
        err = requests.HTTPError("500 Internal Server Error")
        err.response = Mock(status_code=500)
        mock_post.return_value.raise_for_status.side_effect = err
        adapter = OpenAICompatibleAdapter(base_url="http://localhost:1234/v1", model="m")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")


class TestMetaCentrumAdapter:
    """Tests for the MetaCentrum (e-INFRA CZ) hosted LLM gateway provider."""

    def _make_chat_response(self, content: str):
        mock = Mock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"choices": [{"message": {"content": content}}]}
        return mock

    def test_defaults_to_e_infra_base_url(self):
        adapter = MetaCentrumAdapter(api_key="token", model="m")
        assert adapter.endpoint == "https://llm.ai.e-infra.cz/v1"

    def test_default_timeout_is_longer_than_local_providers(self):
        adapter = MetaCentrumAdapter(api_key="token", model="m")
        assert adapter.timeout == 300.0

    def test_base_url_override(self):
        adapter = MetaCentrumAdapter(
            api_key="token", model="m", base_url="https://custom.example/v1"
        )
        assert adapter.endpoint == "https://custom.example/v1"

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_success(self, mock_post):
        mock_post.return_value = self._make_chat_response("hello from metacentrum")
        adapter = MetaCentrumAdapter(api_key="token", model="m")
        assert adapter.generate("prompt") == "hello from metacentrum"
        assert "https://llm.ai.e-infra.cz/v1/chat/completions" in mock_post.call_args.args[0]
        assert mock_post.call_args.kwargs["timeout"] == 300.0

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_sends_bearer_auth(self, mock_post):
        mock_post.return_value = self._make_chat_response("ok")
        adapter = MetaCentrumAdapter(api_key="e-infra-token", model="m")
        adapter.generate("prompt")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer e-infra-token"

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.post")
    def test_generate_raises_on_http_error(self, mock_post):
        err = requests.HTTPError("401 Unauthorized")
        err.response = Mock(status_code=401)
        mock_post.return_value.raise_for_status.side_effect = err
        adapter = MetaCentrumAdapter(api_key="bad-token", model="m")
        with pytest.raises(RuntimeError, match="Failed to generate"):
            adapter.generate("prompt")

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_success(self, mock_get):
        mock_get.return_value.status_code = 200
        adapter = MetaCentrumAdapter(api_key="token", model="m")
        assert adapter.check_health() is True
        assert "https://llm.ai.e-infra.cz/v1/models" in mock_get.call_args.args[0]

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_check_health_failure(self, mock_get):
        mock_get.side_effect = requests.ConnectionError()
        adapter = MetaCentrumAdapter(api_key="token", model="m")
        assert adapter.check_health() is False

    @patch("services.llm_orchestrator.llm_adapters.requests.Session.get")
    def test_list_models(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "data": [{"id": "llama-3.3-70b-instruct"}]
        }
        models = MetaCentrumAdapter(api_key="token", model="m").list_models()
        assert models == ["llama-3.3-70b-instruct"]


class TestLLMFactory:
    """Tests for provider factory behavior."""

    def test_create_ollama_client(self):
        client = create_llm_client("ollama", "http://test:11434", "llama2")
        assert isinstance(client, OllamaAdapter)

    def test_create_openai_client(self):
        client = create_llm_client("openai", "", "gpt-4o-mini", api_key="sk-test")
        assert isinstance(client, OpenAIAdapter)
        assert client.model == "gpt-4o-mini"

    def test_create_anthropic_client(self):
        client = create_llm_client("anthropic", "", "claude-haiku-4-5-20251001", api_key="sk-ant-test")
        assert isinstance(client, AnthropicAdapter)
        assert client.api_key == "sk-ant-test"

    def test_create_openai_compatible_uses_base_url(self):
        client = create_llm_client(
            "openai_compatible", "http://fallback:11434", "local-model",
            base_url="http://localhost:1234/v1",
        )
        assert isinstance(client, OpenAICompatibleAdapter)
        assert client.endpoint == "http://localhost:1234/v1"

    def test_create_openai_compatible_falls_back_to_endpoint_when_no_base_url(self):
        client = create_llm_client("openai_compatible", "http://localhost:8000/v1", "model")
        assert isinstance(client, OpenAICompatibleAdapter)
        assert client.endpoint == "http://localhost:8000/v1"

    def test_create_metacentrum_client_defaults_base_url(self):
        client = create_llm_client("metacentrum", "", "model", api_key="e-infra-token")
        assert isinstance(client, MetaCentrumAdapter)
        assert client.endpoint == "https://llm.ai.e-infra.cz/v1"
        assert client.api_key == "e-infra-token"

    def test_create_metacentrum_client_honors_base_url_override(self):
        client = create_llm_client(
            "metacentrum", "", "model",
            api_key="e-infra-token", base_url="https://custom.example/v1",
        )
        assert isinstance(client, MetaCentrumAdapter)
        assert client.endpoint == "https://custom.example/v1"

    def test_create_llm_client_raises_on_unknown_provider(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_client("unknown_provider", "http://x", "model")

    @pytest.mark.parametrize("provider,expected_type", [
        ("ollama", OllamaAdapter),
        ("openai", OpenAIAdapter),
        ("anthropic", AnthropicAdapter),
        ("openai_compatible", OpenAICompatibleAdapter),
        ("metacentrum", MetaCentrumAdapter),
    ])
    def test_factory_returns_correct_type_per_provider(self, provider, expected_type):
        client = create_llm_client(
            provider, "http://localhost:11434", "model",
            api_key="test-key", base_url="http://localhost:1234/v1",
        )
        assert isinstance(client, expected_type)


@pytest.mark.integration
def test_llm_env_wiring_health_smoke():
    """Non-mocked smoke test for provider/endpoint wiring via environment."""
    provider = os.getenv("LLM_PROVIDER")
    endpoint = os.getenv("LLM_ENDPOINT")
    model = os.getenv("LLM_MODEL")

    if not is_llm_configured(provider) or not endpoint or not model:
        pytest.skip("LLM_PROVIDER/LLM_ENDPOINT/LLM_MODEL not set; skipping runtime wiring smoke test")

    client = create_llm_client(provider=provider, endpoint=endpoint, model=model)
    if not client.check_health():
        pytest.skip("LLM endpoint not reachable in this test context")


class TestPullOllamaModel:
    """The shared /api/pull helper (Stage 18.3)."""

    @staticmethod
    def _response(lines, ok=True, status_code=200, body=None):
        response = Mock(ok=ok, status_code=status_code)
        response.iter_lines.return_value = [line.encode() if isinstance(line, str) else line for line in lines]
        response.json.return_value = body if body is not None else {}
        return response

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_reports_progress_and_finishes(self, mock_post):
        mock_post.return_value = self._response([
            '{"status": "pulling manifest"}',
            '',
            'not json',
            '{"status": "pulling abc", "digest": "abc", "total": 100, "completed": 40}',
            '{"status": "success"}',
        ])
        progress = []

        assert pull_ollama_model("http://ollama:11434/", "gemma3:4b", on_progress=lambda *p: progress.append(p))

        assert progress == [("pulling manifest", 0, 0), ("pulling abc", 40, 100), ("success", 0, 0)]
        assert mock_post.call_args[0][0] == "http://ollama:11434/api/pull"
        assert mock_post.call_args[1]["json"] == {"name": "gemma3:4b", "stream": True}
        mock_post.return_value.close.assert_called_once()

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_cancel_stops_reading_and_closes(self, mock_post):
        mock_post.return_value = self._response(['{"status": "a"}', '{"status": "b"}', '{"status": "success"}'])
        progress = []

        done = pull_ollama_model(
            "http://ollama:11434", "m", on_progress=lambda *p: progress.append(p[0]),
            should_cancel=lambda: len(progress) >= 2,
        )

        assert done is False and progress == ["a", "b"]
        mock_post.return_value.close.assert_called_once()

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_an_http_error_carries_ollamas_message(self, mock_post):
        mock_post.return_value = self._response([], ok=False, status_code=500, body={"error": "pull model manifest: file does not exist"})
        with pytest.raises(RuntimeError, match="file does not exist"):
            pull_ollama_model("http://ollama:11434", "no-such-model")

    @patch('services.llm_orchestrator.llm_adapters.requests.post')
    def test_unreachable_is_a_runtime_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(RuntimeError, match="refused"):
            pull_ollama_model("http://ollama:11434", "m")
