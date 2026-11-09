"""Tests for ui/ollama_models.py."""
from unittest.mock import Mock, patch

import requests

import pytest

from ui import ollama_models
from ui.ollama_models import OLLAMA_CANDIDATES, find_ollama, list_ollama_models
from ui.settings import DEFAULT_ENDPOINT


class TestListOllamaModels:
    @pytest.fixture(autouse=True)
    def _every_host_resolves(self, monkeypatch):
        monkeypatch.setattr(ollama_models, "_resolves", lambda host, timeout: True)

    def test_empty_endpoint_returns_none(self):
        assert list_ollama_models("") is None

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
    def test_connection_error_returns_none(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")
        assert list_ollama_models("http://unreachable:11434") is None

    @patch("requests.get")
    def test_http_error_returns_none(self, mock_get):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = response
        assert list_ollama_models("http://ollama:11434") is None

    @patch("requests.get")
    def test_malformed_json_returns_none(self, mock_get):
        response = Mock(status_code=200)
        response.raise_for_status = lambda: None
        response.json.side_effect = ValueError("not json")
        mock_get.return_value = response
        assert list_ollama_models("http://ollama:11434") is None

    @patch("requests.get")
    def test_entries_without_name_are_skipped(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"models": [{"name": "llama3:8b"}, {"digest": "abc"}]},
            raise_for_status=lambda: None,
        )
        assert list_ollama_models("http://ollama:11434") == ["llama3:8b"]

    def test_a_malformed_endpoint_returns_none(self):
        # urlsplit raised ValueError, so the dialog's search ended with
        # neither the found status nor the Re-check panel.
        assert list_ollama_models("http://[::1:11434") is None

    @patch("requests.get")
    def test_a_reply_that_is_not_ollamas_returns_none_or_skips_entries(self, mock_get):
        reply = {"models": "nope"}
        mock_get.return_value = Mock(status_code=200, json=lambda: reply, raise_for_status=lambda: None)
        assert list_ollama_models("http://ollama:11434") is None
        reply["models"] = ["text", {"name": "gemma3:4b"}]
        assert list_ollama_models("http://ollama:11434") == ["gemma3:4b"]

    @patch("requests.get")
    def test_reachable_without_models_returns_empty_list(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {}, raise_for_status=lambda: None
        )
        assert list_ollama_models("http://ollama:11434") == []


class TestResolves:
    def test_a_slow_lookup_counts_as_unreachable(self, monkeypatch):
        import time

        monkeypatch.setattr(ollama_models.socket, "getaddrinfo", lambda *a: time.sleep(2))
        started = time.monotonic()
        assert ollama_models._resolves("ollama", timeout=0.1) is False
        assert time.monotonic() - started < 1

    def test_an_unknown_name_and_a_known_one(self, monkeypatch):
        def getaddrinfo(host, port):
            if host != "ollama":
                raise ollama_models.socket.gaierror("Name or service not known")
            return [("addr",)]

        monkeypatch.setattr(ollama_models.socket, "getaddrinfo", getaddrinfo)
        assert ollama_models._resolves("ollama", timeout=1) is True
        assert ollama_models._resolves("nope", timeout=1) is False
        assert ollama_models._resolves("", timeout=1) is False

    def test_an_unresolved_endpoint_is_not_asked(self, monkeypatch):
        monkeypatch.setattr(ollama_models, "_resolves", lambda host, timeout: False)
        with patch("requests.get") as get:
            assert list_ollama_models("http://ollama:11434") is None
        get.assert_not_called()


class TestFindOllama:
    @pytest.fixture
    def answering(self, monkeypatch):
        """Only the endpoints in the returned dict answer; records the asked."""
        servers, asked = {}, []

        def list_models(endpoint, timeout=3.0):
            asked.append(endpoint)
            return servers.get(endpoint)

        monkeypatch.setattr(ollama_models, "list_ollama_models", list_models)
        return servers, asked

    def test_the_given_endpoint_answers_first(self, answering):
        servers, asked = answering
        servers["http://ollama:11434"] = ["gemma3:4b"]
        assert find_ollama("http://ollama:11434/") == ("http://ollama:11434", ["gemma3:4b"])
        assert asked == ["http://ollama:11434"]

    def test_a_default_endpoint_falls_back_to_the_candidates_in_order(self, answering):
        servers, asked = answering
        servers["http://host.docker.internal:11434"] = []
        assert find_ollama("http://ollama:11434") == ("http://host.docker.internal:11434", [])
        assert asked == ["http://ollama:11434", "http://host.docker.internal:11434"]

    def test_the_settings_default_endpoint_also_falls_back(self, answering):
        servers, asked = answering
        servers["http://ollama:11434"] = ["m"]
        assert find_ollama(DEFAULT_ENDPOINT) == ("http://ollama:11434", ["m"])

    def test_a_user_endpoint_is_never_swapped(self, answering):
        servers, asked = answering
        servers["http://ollama:11434"] = ["m"]
        assert find_ollama("http://gpu-box:11434") is None
        assert asked == ["http://gpu-box:11434"]

    def test_nothing_answers(self, answering):
        _, asked = answering
        assert find_ollama("") is None
        assert asked == list(OLLAMA_CANDIDATES)


class TestPullModel:
    def test_one_download_at_a_time(self, monkeypatch):
        seen = []

        def pull(endpoint, name, on_progress, should_cancel):
            seen.append(name)
            # A second download while this one runs is refused.
            with pytest.raises(RuntimeError, match="already running"):
                ollama_models.pull_model(endpoint, "other", on_progress, should_cancel)
            return True

        monkeypatch.setattr(ollama_models, "pull_ollama_model", pull)
        assert ollama_models.pull_model("http://ollama:11434", "gemma3:4b", print, lambda: False)
        assert seen == ["gemma3:4b"]
        # The lock is released afterwards, also after a failure.
        monkeypatch.setattr(
            ollama_models, "pull_ollama_model", Mock(side_effect=RuntimeError("disk full"))
        )
        with pytest.raises(RuntimeError, match="disk full"):
            ollama_models.pull_model("http://ollama:11434", "x", print, lambda: False)
        monkeypatch.setattr(ollama_models, "pull_ollama_model", lambda *a, **k: True)
        assert ollama_models.pull_model("http://ollama:11434", "x", print, lambda: False)
