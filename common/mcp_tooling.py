"""Shared helpers for concise MCP tool and parameter declarations."""

from __future__ import annotations

from typing import Any, Sequence

from common.mcp_base import MCPServer, MCPTool
from common.schemas import MCPToolParameter


def tool_param(
    name: str,
    param_type: str,
    description: str,
    required: bool = False,
) -> MCPToolParameter:
    """Create an MCP tool parameter with a compact call site."""
    return MCPToolParameter(
        name=name,
        type=param_type,
        description=description,
        required=required,
    )


def build_tool_parameters(
    specs: Sequence[tuple[str, str, str] | tuple[str, str, str, bool]],
) -> list[MCPToolParameter]:
    """
    Build MCP tool parameters from compact tuple specs.

    Supported tuple forms:
    - (name, type, description)
    - (name, type, description, required)
    """
    parameters: list[MCPToolParameter] = []
    for spec in specs:
        if len(spec) == 3:
            name, param_type, description = spec
            required = False
        elif len(spec) == 4:
            name, param_type, description, required = spec
        else:
            raise ValueError(f"Invalid parameter spec length: {len(spec)}")
        parameters.append(tool_param(name, param_type, description, required))
    return parameters


def register_mcp_tool(
    server: MCPServer,
    *,
    name: str,
    description: str,
    handler: Any,
    parameters: Sequence[MCPToolParameter],
    returns: dict[str, Any] | None = None,
) -> MCPTool:
    """Create and register an MCP tool on the provided server."""
    tool = MCPTool(
        name=name,
        description=description,
        parameters=list(parameters),
        handler=handler,
        returns=returns,
    )
    server.register_tool(tool)
    return tool
