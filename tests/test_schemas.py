"""
Unit tests for common schemas.
"""
import pytest
from common.schemas import (
    JobSpec, ProteinSpec, LigandSpec, PreparationSpec,
    DockingSpec, VisualizationSpec, MCPToolDefinition,
    MCPToolParameter, get_job_spec_json_schema
)


def test_protein_spec():
    """Test ProteinSpec creation."""
    protein = ProteinSpec(protein_id="1ABC")
    assert protein.protein_id == "1ABC"
    assert protein.format == "pdb"


def test_ligand_spec():
    """Test LigandSpec creation."""
    ligands = LigandSpec(ligand_ids=["LIG1", "LIG2"])
    assert len(ligands.ligand_ids) == 2
    assert ligands.format == "sdf"


def test_preparation_spec_defaults():
    """Test PreparationSpec with defaults."""
    prep = PreparationSpec()
    assert prep.ph == 7.4
    assert prep.output_format == "pdbqt"
    assert prep.preserve_chirality is True


def test_docking_spec():
    """Test DockingSpec creation."""
    docking = DockingSpec(
        center=[1.0, 2.0, 3.0],
        exhaustiveness=10,
        top_n=5
    )
    assert docking.center == [1.0, 2.0, 3.0]
    assert docking.exhaustiveness == 10
    assert docking.top_n == 5


def test_job_spec_complete():
    """Test complete JobSpec creation."""
    job = JobSpec(
        job_id="test_job_001",
        protein=ProteinSpec(protein_id="1ABC"),
        ligands=LigandSpec(ligand_ids=["LIG1"])
    )
    assert job.job_id == "test_job_001"
    assert job.protein.protein_id == "1ABC"
    assert len(job.ligands.ligand_ids) == 1
    assert job.preparation.ph == 7.4  # Default
    assert job.docking.exhaustiveness == 8  # Default


def test_job_spec_json_serialization():
    """Test JobSpec JSON serialization."""
    job = JobSpec(
        job_id="test_job_002",
        protein=ProteinSpec(protein_id="2XYZ"),
        ligands=LigandSpec(ligand_ids=["MOL1", "MOL2"])
    )
    json_data = job.model_dump()
    assert json_data["job_id"] == "test_job_002"
    assert json_data["protein"]["protein_id"] == "2XYZ"


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


def test_get_job_spec_json_schema():
    """Test JSON Schema generation for JobSpec."""
    schema = get_job_spec_json_schema()
    assert "$defs" in schema or "definitions" in schema
    assert "properties" in schema
    assert "job_id" in schema["properties"]
