"""
Shared JSON-RPC MCP client helpers.
"""
from typing import Any, Callable, Dict, Optional

from common.http_retry import build_mcp_retry_session

# Retries only connection-establishment failures, never read timeouts/5xx --
# see common/http_retry.py for why that distinction matters for a call that
# can trigger a real side-effecting action (e.g. run_docking).
_session = build_mcp_retry_session()


def call_mcp_tool(
    service_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    request_id: str = "1",
    raise_on_error: bool = True,
    timeout: int = 300,
    wrap_connection_errors: bool = False,
    on_request: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_response: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Call an MCP tool via JSON-RPC 2.0 and return the result object.

    wrap_connection_errors=False (default) preserves this function's
    original behavior of letting requests.RequestException propagate
    as-is -- some callers (e.g. example_complete_workflow.py) catch
    requests.exceptions.ConnectionError specifically for a friendlier
    message, which a wrapped exception type would no longer match. Pass
    True to instead re-raise as RuntimeError(f"Failed to call {tool_name}: ...")
    (WorkflowOrchestrator's historical behavior).

    on_request/on_response are optional hooks invoked with the raw
    request/response JSON-RPC payloads, letting a caller (e.g.
    WorkflowOrchestrator's MCP-demo verbosity mode) print full traffic
    without duplicating this function's request-building/error-handling.
    """
    endpoint = service_url if service_url.endswith("/mcp") else f"{service_url}/mcp"

    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tool_call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    if on_request:
        on_request(payload)

    if wrap_connection_errors:
        import requests

        try:
            response = _session.post(endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to call {tool_name}: {e}") from e
    else:
        response = _session.post(endpoint, json=payload, timeout=timeout)
        response.raise_for_status()

    data = response.json()

    if on_response:
        on_response(data)

    error = data.get("error")
    if error:
        if raise_on_error:
            raise RuntimeError(f"MCP Error: {error.get('message', 'Unknown error')}")
        return None

    return data.get("result")
