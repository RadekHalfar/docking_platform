"""
Pydantic models for MCP tool definitions (the tools/list response).
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class MCPToolParameter(BaseModel):
    """MCP tool parameter definition."""
    name: str
    type: str
    description: str
    # Matches common.mcp_tooling.tool_param()'s default -- every parameter
    # in this codebase is declared through that helper (or
    # build_tool_parameters()'s 3-tuple form, which defaults the same way),
    # so this model's own default previously disagreed with how it's
    # actually always constructed in practice.
    required: bool = False
    default: Optional[Any] = None


class MCPToolDefinition(BaseModel):
    """MCP tool definition for tools/list response."""
    name: str
    description: str
    parameters: List[MCPToolParameter]
    returns: Dict[str, Any]

