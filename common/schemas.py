"""
JSON Schema definitions for job specifications and MCP messages.

JobSpec: Structured representation of a parsed natural language command.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProteinSpec(BaseModel):
    """Specification for protein structure."""
    protein_id: str = Field(..., description="PDB ID (e.g., '1ABC')")
    format: str = Field(default="pdb", description="File format (pdb, mmcif)")


class LigandSpec(BaseModel):
    """Specification for ligand molecules."""
    ligand_ids: List[str] = Field(..., description="List of ligand identifiers")
    format: str = Field(default="sdf", description="File format (sdf, mol, mol2)")


class PreparationSpec(BaseModel):
    """Specification for ligand preparation."""
    ph: float = Field(default=7.4, description="pH for protonation")
    output_format: str = Field(default="pdbqt", description="Output format")
    preserve_chirality: bool = Field(default=True, description="Preserve chirality")
    add_hydrogens: bool = Field(default=True, description="Add hydrogens")


class DockingSpec(BaseModel):
    """Specification for docking parameters."""
    center: Optional[List[float]] = Field(default=None, description="Search box center [x,y,z]")
    size: Optional[List[float]] = Field(default=[20.0, 20.0, 20.0], description="Search box size")
    exhaustiveness: int = Field(default=8, description="Exhaustiveness parameter")
    top_n: int = Field(default=3, description="Number of top poses to return")
    num_modes: int = Field(default=9, description="Number of binding modes")


class VisualizationSpec(BaseModel):
    """Specification for visualization."""
    top_n: int = Field(default=3, description="Number of top poses to visualize")
    png_size: List[int] = Field(default=[1920, 1080], description="Image dimensions")
    colors: Optional[Dict[str, str]] = Field(default=None, description="Color scheme")
    camera_angles: Optional[List[str]] = Field(default=None, description="Camera presets")


class JobSpec(BaseModel):
    """
    Complete job specification extracted from natural language.
    
    This is the structured representation that the orchestrator
    creates from parsing natural language commands.
    """
    job_id: str = Field(..., description="Unique job identifier")
    protein: ProteinSpec
    ligands: LigandSpec
    preparation: PreparationSpec = Field(default_factory=PreparationSpec)
    docking: DockingSpec = Field(default_factory=DockingSpec)
    visualization: VisualizationSpec = Field(default_factory=VisualizationSpec)
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job_2025_001",
                "protein": {"protein_id": "1ABC", "format": "pdb"},
                "ligands": {"ligand_ids": ["LIG1", "LIG2"], "format": "sdf"},
                "preparation": {"ph": 7.4, "output_format": "pdbqt"},
                "docking": {
                    "center": [12.3, 34.5, -5.6],
                    "size": [20, 20, 20],
                    "exhaustiveness": 8,
                    "top_n": 3
                },
                "visualization": {"top_n": 3, "png_size": [1920, 1080]}
            }
        }
    }


# JSON-RPC 2.0 Message Schemas
class MCPToolParameter(BaseModel):
    """MCP tool parameter definition."""
    name: str
    type: str
    description: str
    required: bool = True
    default: Optional[Any] = None


class MCPToolDefinition(BaseModel):
    """MCP tool definition for tools/list response."""
    name: str
    description: str
    parameters: List[MCPToolParameter]
    returns: Dict[str, Any]


class MCPToolCallRequest(BaseModel):
    """JSON-RPC request for tool_call."""
    jsonrpc: str = "2.0"
    id: str
    method: str = "tool_call"
    params: Dict[str, Any]  # Contains 'name' and 'arguments'


class MCPToolCallResponse(BaseModel):
    """JSON-RPC response for tool_call."""
    jsonrpc: str = "2.0"
    id: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


class MCPError(BaseModel):
    """JSON-RPC error object."""
    code: int
    message: str
    data: Optional[Any] = None


# Export job spec schema as JSON Schema
def get_job_spec_json_schema() -> Dict[str, Any]:
    """Get JobSpec as JSON Schema for documentation."""
    return JobSpec.model_json_schema()
