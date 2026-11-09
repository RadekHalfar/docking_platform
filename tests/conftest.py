"""Shared pytest fixtures for test-suite mock setup."""

import os
from unittest.mock import Mock

import pytest


@pytest.fixture
def llm_client():
    """Provide a generic mock LLM client with a .generate method."""
    return Mock()


@pytest.fixture
def make_mcp_response():
    """Factory for standard JSON-RPC MCP HTTP responses."""

    def _make(*, result=None, error=None, request_id="test", status_code=200):
        response = Mock()
        response.status_code = status_code
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
        }
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        response.json.return_value = payload
        response.raise_for_status = Mock()
        return response

    return _make


@pytest.fixture
def umask_022():
    """Run with the usual umask, under which a plain mkdir gives 0o755.

    For the shared-directory checks: several uids
    write a session (the non-root GUI, the services, root containers), so
    shared directories must be 0o777
    whatever the umask."""
    old = os.umask(0o022)
    yield
    os.umask(old)
