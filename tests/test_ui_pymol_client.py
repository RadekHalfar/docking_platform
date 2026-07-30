"""Tests for ui.pymol_client — the HTTP client extracted from ui/app.py so
this logic (unlike the rest of ui/app.py, which executes top-level Streamlit
calls at import time) can be unit-tested."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from ui.pymol_client import get_load_status, kickoff_load_job


def _make_response(json_data, ok=True, status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data
    return resp


class TestKickoffLoadJob:
    @patch("ui.pymol_client.requests.post")
    def test_posts_correct_url_and_payload(self, mock_post):
        mock_post.return_value = _make_response({"status": "started", "job_id": "job_1"})

        result = kickoff_load_job("http://pymol-interactive:8090", "job_1")

        mock_post.assert_called_once_with(
            "http://pymol-interactive:8090/api/load_job",
            json={"job_id": "job_1"},
            timeout=10,
        )
        assert result == {"status": "started", "job_id": "job_1"}

    @patch("ui.pymol_client.requests.post")
    def test_non_ok_response_becomes_error_dict(self, mock_post):
        mock_post.return_value = _make_response(
            {"error": "bad request"}, ok=False, status_code=400, text="bad request"
        )

        result = kickoff_load_job("http://pymol-interactive:8090", "job_1")

        assert result["status"] == "error"
        assert "400" in result["error"]

    @patch("ui.pymol_client.requests.post")
    def test_connection_error_becomes_error_dict_not_raised(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")

        result = kickoff_load_job("http://pymol-interactive:8090", "job_1")

        assert result["status"] == "error"
        assert "refused" in result["error"]

    @patch("ui.pymol_client.requests.post")
    def test_timeout_becomes_error_dict_not_raised(self, mock_post):
        mock_post.side_effect = requests.Timeout("timed out")

        result = kickoff_load_job("http://pymol-interactive:8090", "job_1")

        assert result["status"] == "error"
        assert "timed out" in result["error"]


class TestGetLoadStatus:
    @patch("ui.pymol_client.requests.get")
    def test_gets_correct_url(self, mock_get):
        mock_get.return_value = _make_response({"status": "running", "job_id": "job_1"})

        result = get_load_status("http://pymol-interactive:8090")

        mock_get.assert_called_once_with(
            "http://pymol-interactive:8090/api/load_status", timeout=10
        )
        assert result == {"status": "running", "job_id": "job_1"}

    @patch("ui.pymol_client.requests.get")
    def test_non_ok_response_becomes_error_dict(self, mock_get):
        mock_get.return_value = _make_response({}, ok=False, status_code=500, text="boom")

        result = get_load_status("http://pymol-interactive:8090")

        assert result["status"] == "error"
        assert "500" in result["error"]

    @patch("ui.pymol_client.requests.get")
    def test_connection_error_becomes_error_dict_not_raised(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        result = get_load_status("http://pymol-interactive:8090")

        assert result["status"] == "error"
        assert "refused" in result["error"]
