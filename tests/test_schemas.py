"""
Unit tests for common schemas.
"""
from common.schemas import MCPToolDefinition, MCPToolParameter


def test_mcp_tool_parameter():
    """Test MCPToolParameter creation."""
    param = MCPToolParameter(
        name="protein_id",
        type="string",
        description="PDB identifier",
        required=True
    )
    assert param.name == "protein_id"
    assert param.required is True


def test_mcp_tool_definition():
    """Test MCPToolDefinition creation."""
    params = [
        MCPToolParameter(
            name="protein_id",
            type="string",
            description="PDB ID",
            required=True
        )
    ]
    tool_def = MCPToolDefinition(
        name="download_structures",
        description="Download protein structures",
        parameters=params,
        returns={"protein_path": "string"}
    )
    assert tool_def.name == "download_structures"
    assert len(tool_def.parameters) == 1

