"""Tests for common.mcp_client.call_mcp_tool (shared MCP JSON-RPC client)."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from common.mcp_client import call_mcp_tool


def _mock_response(json_body, status_ok=True):
    resp = Mock()
    resp.json.return_value = json_body
    if status_ok:
        resp.raise_for_status = Mock()
    else:
        resp.raise_for_status = Mock(side_effect=requests.HTTPError("500 error"))
    return resp


class TestCallMcpTool:
    def test_appends_mcp_suffix_to_endpoint(self):
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {"ok": True}})
            call_mcp_tool("http://localhost:8001", "download_structures", {})
            assert mock_post.call_args[0][0] == "http://localhost:8001/mcp"

    def test_does_not_double_append_mcp_suffix(self):
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {}})
            call_mcp_tool("http://localhost:8001/mcp", "x", {})
            assert mock_post.call_args[0][0] == "http://localhost:8001/mcp"

    def test_returns_result_on_success(self):
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {"status": "ok"}})
            result = call_mcp_tool("http://x", "tool", {})
            assert result == {"status": "ok"}

    def test_raises_on_error_by_default(self):
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"error": {"message": "boom"}})
            with pytest.raises(RuntimeError, match="MCP Error: boom"):
                call_mcp_tool("http://x", "tool", {})

    def test_returns_none_on_error_when_raise_on_error_false(self):
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"error": {"message": "boom"}})
            result = call_mcp_tool("http://x", "tool", {}, raise_on_error=False)
            assert result is None

    def test_connection_error_propagates_raw_by_default(self):
        """Default (wrap_connection_errors=False) preserves the original
        behavior some callers depend on -- example_complete_workflow.py
        catches requests.exceptions.ConnectionError specifically for a
        friendlier message; a wrapped RuntimeError would no longer match."""
        with patch("common.mcp_client._session.post", side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(requests.exceptions.ConnectionError):
                call_mcp_tool("http://x", "tool", {})

    def test_connection_error_wrapped_when_opted_in(self):
        with patch("common.mcp_client._session.post", side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(RuntimeError, match="Failed to call tool"):
                call_mcp_tool("http://x", "tool", {}, wrap_connection_errors=True)

    def test_on_request_hook_receives_the_payload(self):
        seen = []
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {}})
            call_mcp_tool(
                "http://x", "my_tool", {"a": 1}, request_id="req-1",
                on_request=seen.append,
            )
        assert len(seen) == 1
        assert seen[0]["params"]["name"] == "my_tool"
        assert seen[0]["params"]["arguments"] == {"a": 1}
        assert seen[0]["id"] == "req-1"

    def test_on_response_hook_receives_the_raw_response(self):
        seen = []
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {"x": 1}})
            call_mcp_tool("http://x", "tool", {}, on_response=seen.append)
        assert seen == [{"result": {"x": 1}}]

    def test_on_response_hook_still_called_when_result_is_an_error(self):
        seen = []
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"error": {"message": "boom"}})
            with pytest.raises(RuntimeError):
                call_mcp_tool("http://x", "tool", {}, on_response=seen.append)
        assert seen == [{"error": {"message": "boom"}}]

    def test_default_request_id_is_static_one(self):
        seen = []
        with patch("common.mcp_client._session.post") as mock_post:
            mock_post.return_value = _mock_response({"result": {}})
            call_mcp_tool("http://x", "tool", {}, on_request=seen.append)
        assert seen[0]["id"] == "1"
