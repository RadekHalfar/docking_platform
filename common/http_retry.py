"""Shared HTTP retry/backoff policy for LLM and MCP calls.

A single transient network blip (common with local Ollama warm-up, or a
Docker service restarting) shouldn't abort the whole request. These sessions
retry connection-level failures and (for the
LLM session only) read timeouts / a small set of transient status codes,
with exponential backoff between attempts.

Two separate policies, not one, because retrying is only safe when the
retried request can't have already taken effect server-side:
- LLM generate() calls are treated as safe to retry more liberally (worst
  case on a false retry is a wasted/duplicate generation, not a
  side-effecting action).
- MCP tool calls (build_mcp_retry_session) can trigger real work (run a
  docking job, prepare ligands, ...) that must not fire twice, so only
  connection-establishment failures are retried -- those mean the request
  never reached the server at all. A read timeout or 5xx is NOT retried
  here, since the server may already have received and started acting on
  the request even though the client didn't get a response in time.
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5  # ~0.5s, 1s, 2s between attempts
_TRANSIENT_STATUS_CODES = (502, 503, 504)


def build_llm_retry_session(
    retries: int = DEFAULT_RETRIES, backoff_factor: float = DEFAULT_BACKOFF_FACTOR
) -> requests.Session:
    """Session for LLM provider calls: retries connection failures, read
    timeouts, and transient 502/503/504 responses. Never retries 4xx (a bad
    request/auth failure won't be fixed by trying again)."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff_factor,
        status_forcelist=_TRANSIENT_STATUS_CODES,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def build_mcp_retry_session(
    retries: int = DEFAULT_RETRIES, backoff_factor: float = DEFAULT_BACKOFF_FACTOR
) -> requests.Session:
    """Session for MCP tool calls: retries ONLY connection-establishment
    failures (DNS/refused/etc, meaning the request never reached the
    server) -- never read timeouts or 5xx, since a tool call can be a real
    side-effecting action (e.g. run_docking) that must not be repeated just
    because the response was slow to arrive."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=0,
        status=0,
        backoff_factor=backoff_factor,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
