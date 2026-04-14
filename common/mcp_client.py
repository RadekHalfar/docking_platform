"""
Shared JSON-RPC MCP client helpers.
"""
from typing import Any, Dict, Optional

import requests


def call_mcp_tool(
    service_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    request_id: str = "1",
    raise_on_error: bool = True,
    timeout: int = 300,
) -> Optional[Dict[str, Any]]:
    """Call an MCP tool via JSON-RPC 2.0 and return the result object."""
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

    response = requests.post(endpoint, json=payload, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    error = data.get("error")
    if error:
        if raise_on_error:
            raise RuntimeError(f"MCP Error: {error.get('message', 'Unknown error')}")
        return None

    return data.get("result")
