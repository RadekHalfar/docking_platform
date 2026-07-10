"""Tests for ui/ollama_models.py."""
from unittest.mock import Mock, patch

import requests

from ui.ollama_models import list_ollama_models


class TestListOllamaModels:
    def test_empty_endpoint_returns_empty_list(self):
        assert list_ollama_models("") == []

    @patch("requests.get")
    def test_returns_sorted_model_names(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"models": [{"name": "mistral:latest"}, {"name": "gemma3:4b"}]},
            raise_for_status=lambda: None,
        )

        models = list_ollama_models("http://ollama:11434")

        assert models == ["gemma3:4b", "mistral:latest"]

    @patch("requests.get")
    def test_calls_correct_tags_endpoint(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {"models": []}, raise_for_status=lambda: None
        )

        list_ollama_models("http://ollama:11434/")

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://ollama:11434/api/tags"

    @patch("requests.get")
    def test_connection_error_returns_empty_list(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")
        assert list_ollama_models("http://unreachable:11434") == []

    @patch("requests.get")
    def test_http_error_returns_empty_list(self, mock_get):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = response
        assert list_ollama_models("http://ollama:11434") == []

    @patch("requests.get")
    def test_malformed_json_returns_empty_list(self, mock_get):
        response = Mock(status_code=200)
        response.raise_for_status = lambda: None
        response.json.side_effect = ValueError("not json")
        mock_get.return_value = response
        assert list_ollama_models("http://ollama:11434") == []

    @patch("requests.get")
    def test_entries_without_name_are_skipped(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"models": [{"name": "llama3:8b"}, {"digest": "abc"}]},
            raise_for_status=lambda: None,
        )
        assert list_ollama_models("http://ollama:11434") == ["llama3:8b"]

    @patch("requests.get")
    def test_no_models_key_returns_empty_list(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {}, raise_for_status=lambda: None
        )
        assert list_ollama_models("http://ollama:11434") == []
