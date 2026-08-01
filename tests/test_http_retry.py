"""Tests for the shared HTTP retry/backoff session builders.

These check the configured Retry policy itself (total attempts, which
failure classes are retried) rather than simulating real network retries
end-to-end -- the call-site tests (test_llm_adapters.py,
test_llm_orchestrator.py) already cover error-handling behavior with
Session.post/get mocked, which bypasses the retry machinery entirely by
design (that's the layer being mocked out).
"""
from __future__ import annotations

from common.http_retry import build_llm_retry_session, build_mcp_retry_session


def _retry_config(session):
    adapter = session.get_adapter("https://example.com")
    return adapter.max_retries


class TestLlmRetrySession:
    def test_retries_connection_and_read_failures(self):
        retry = _retry_config(build_llm_retry_session())
        assert retry.total == 3
        assert retry.connect == 3
        assert retry.read == 3

    def test_retries_transient_status_codes_not_client_errors(self):
        retry = _retry_config(build_llm_retry_session())
        assert 503 in retry.status_forcelist
        assert 502 in retry.status_forcelist
        assert 504 in retry.status_forcelist
        # 4xx must never be in the retry set -- retrying a bad request/auth
        # failure just wastes time, it will never succeed.
        assert 400 not in retry.status_forcelist
        assert 401 not in retry.status_forcelist
        assert 404 not in retry.status_forcelist

    def test_custom_retry_count_is_respected(self):
        retry = _retry_config(build_llm_retry_session(retries=5))
        assert retry.total == 5
        assert retry.connect == 5


class TestMcpRetrySession:
    def test_retries_only_connection_failures(self):
        """A tool call can trigger a real side-effecting action (e.g.
        run_docking); retrying a read timeout or 5xx risks firing it twice,
        so only connection-establishment failures (request never reached
        the server) are retried here."""
        retry = _retry_config(build_mcp_retry_session())
        assert retry.total == 3
        assert retry.connect == 3
        assert retry.read == 0
        assert retry.status == 0

    def test_does_not_retry_any_status_code(self):
        retry = _retry_config(build_mcp_retry_session())
        assert not retry.status_forcelist


class TestSessionsAreIndependent:
    def test_each_call_returns_a_fresh_session(self):
        """Sessions carry connection pools -- callers must each get their
        own, not share mutable state."""
        a = build_mcp_retry_session()
        b = build_mcp_retry_session()
        assert a is not b
