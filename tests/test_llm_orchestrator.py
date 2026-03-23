"""
Tests for LLM Orchestrator

Unit and integration tests for natural language workflow orchestration.
"""
import pytest
import json
import platform
from unittest.mock import Mock, patch
from pathlib import Path

from services.llm_orchestrator import (
    WorkflowOrchestrator,
    WorkflowParser,
    create_llm_client,
    parse_workflow_with_llm,
)


class TestLLMArchitecture:
    """Test provider-agnostic LLM architecture."""

    def test_factory_creates_provider_adapter(self):
        """Factory should return a configured adapter for supported provider."""
        client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        assert client.endpoint == "http://test:11434"
        assert client.model == "llama2"

    def test_factory_rejects_unknown_provider(self):
        """Factory should raise for unsupported providers."""
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_client(provider="unknown", endpoint="http://test", model="demo")

    def test_parse_workflow_with_llm(self):
        """Provider-neutral parser should parse JSON returned by adapter."""
        workflow_json = {
            "services": ["download", "dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
                "center": [10, 20, 30],
                "box_size": [25, 30, 35]
            },
            "missing_required": []
        }

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(workflow_json)):
            result = parse_workflow_with_llm(llm_client, "Download ATP and dock to 6lu7")

        assert result["services"] == ["download", "dock"]
        assert result["parameters"]["protein_id"] == "6lu7"
        assert "center" in result["parameters"]

    def test_parse_workflow_visualization_fallback(self):
        """Visualization keyword fallback should append visualize service."""
        llm_output = {
            "services": ["download", "prepare_ligands", "prepare_protein", "dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"]
            },
            "missing_required": []
        }

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(llm_output)):
            result = parse_workflow_with_llm(llm_client, "Download 6lu7 and ATP, then render image")

        assert "visualize" in result["services"]


class TestWorkflowParser:
    """Test workflow parser and validator."""
    
    def test_validate_workflow_basic(self):
        """Test basic workflow validation."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["download"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"]
            }
        }
        
        result = parser.validate_workflow(workflow)
        
        assert "services" in result
        assert "parameters" in result
        assert "missing_required" in result
        assert result["parameters"]["ph"] == 7.4  # Default applied
    
    def test_validate_workflow_with_defaults(self):
        """Test that defaults are applied."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
                "center": [10, 20, 30],
                "box_size": [25, 30, 35]
            }
        }
        
        result = parser.validate_workflow(workflow)
        
        assert result["parameters"]["exhaustiveness"] == 8
        assert result["parameters"]["cpu"] == 4
        assert result["parameters"]["population"] == 500
    
    def test_missing_required_parameters(self):
        """Test detection of missing required parameters."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"]
                # Missing center and box_size
            }
        }
        
        result = parser.validate_workflow(workflow)
        
        assert parser.has_missing_parameters()
        assert "center" in result["missing_required"]
        assert "box_size" in result["missing_required"]

    def test_empty_docking_coordinates_are_treated_as_missing(self):
        """Empty coordinate arrays from LLM output should be treated as missing."""
        parser = WorkflowParser()

        workflow = {
            "services": ["dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
                "center": [],
                "box_size": []
            }
        }

        result = parser.validate_workflow(workflow)

        assert "center" in result["missing_required"]
        assert "box_size" in result["missing_required"]
    
    def test_update_parameters(self):
        """Test parameter updates."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["dock"],
            "parameters": {"protein_id": "6lu7"},
            "missing_required": ["center", "box_size"]
        }
        
        parser.parsed_workflow = workflow
        
        parser.update_parameters({
            "center": [10, 20, 30],
            "box_size": [25, 30, 35]
        })
        
        assert parser.parsed_workflow["parameters"]["center"] == [10, 20, 30]
        assert "center" not in parser.get_missing_parameters()
    
    def test_validate_protein_id(self):
        """Test PDB ID validation."""
        parser = WorkflowParser()
        
        assert parser.validate_protein_id("6lu7") is True
        assert parser.validate_protein_id("1ABC") is True
        assert parser.validate_protein_id("123") is False  # Too short
        assert parser.validate_protein_id("12345") is False  # Too long
        assert parser.validate_protein_id("AB-C") is False  # Invalid chars
    
    def test_validate_coordinates(self):
        """Test coordinate validation."""
        parser = WorkflowParser()
        
        assert parser.validate_coordinates([10.5, 20.3, -5.2]) is True
        assert parser.validate_coordinates([10, 20, 30]) is True
        assert parser.validate_coordinates([10, 20]) is False  # Too few
        assert parser.validate_coordinates([10, 20, 30, 40]) is False  # Too many
        assert parser.validate_coordinates("10,20,30") is False  # Wrong type
    
    def test_get_workflow_summary(self):
        """Test workflow summary generation."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["download", "dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP", "HEM"],
                "center": [10, 20, 30],
                "box_size": [25, 30, 35],
                "ph": 7.4
            },
            "missing_required": []
        }
        
        parser.parsed_workflow = workflow
        summary = parser.get_workflow_summary()
        
        assert "6lu7" in summary
        assert "ATP" in summary
        assert "[10, 20, 30]" in summary


class TestWorkflowOrchestrator:
    """Test workflow orchestrator."""
    
    def test_init(self):
        """Test orchestrator initialization."""
        orchestrator = WorkflowOrchestrator(output_base="./test_output")
        
        assert orchestrator.output_base == Path("./test_output").resolve()
        assert "rcsb_downloader" in orchestrator.services
        assert "moldina_runner" in orchestrator.services
    
    @patch('services.llm_orchestrator.orchestrator.requests.get')
    def test_check_services_health(self, mock_get):
        """Test service health checking."""
        mock_get.return_value.status_code = 200
        
        orchestrator = WorkflowOrchestrator()
        health = orchestrator.check_services_health()
        
        assert isinstance(health, dict)
        assert "rcsb_downloader" in health
    
    def test_normalize_path(self):
        """Test path normalization."""
        orchestrator = WorkflowOrchestrator()
        
        # Windows path
        result = orchestrator.normalize_path("C:\\Users\\test\\file.txt")
        assert "\\" not in result
        assert result.startswith("C:/")
        
        # Unix path handling is platform-dependent
        result = orchestrator.normalize_path("/home/test/file.txt")
        if platform.system() == "Windows":
            # On Windows, Path("/home/test") becomes "C:/home/test" or similar
            assert "/home/test/file.txt" in result
        else:
            assert result == "/home/test/file.txt"
    
    @patch('services.llm_orchestrator.orchestrator.requests.post')
    def test_call_mcp_tool(self, mock_post):
        """Test MCP tool calling."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "jsonrpc": "2.0",
            "id": "test",
            "result": {"success": True}
        }
        
        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "test_job"
        
        result = orchestrator.call_mcp_tool(
            "http://localhost:8001",
            "test_tool",
            {"param": "value"}
        )
        
        assert result["success"] is True
    
    @patch('services.llm_orchestrator.orchestrator.requests.post')
    def test_call_mcp_tool_error(self, mock_post):
        """Test MCP tool error handling."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "jsonrpc": "2.0",
            "id": "test",
            "error": {"code": -32000, "message": "Tool failed"}
        }
        
        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "test_job"
        
        with pytest.raises(RuntimeError, match="Tool failed"):
            orchestrator.call_mcp_tool(
                "http://localhost:8001",
                "test_tool",
                {"param": "value"}
            )


class TestIntegration:
    """Integration tests for full workflow."""
    
    @patch('services.llm_orchestrator.orchestrator.requests.post')
    def test_full_workflow_mock(self, mock_mcp_post):
        """Test full workflow with mocked services."""
        workflow_json = {
            "services": ["download"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"]
            },
            "missing_required": []
        }
        
        # Mock MCP responses
        mock_mcp_response = Mock()
        mock_mcp_response.status_code = 200
        mock_mcp_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "test",
            "result": {
                "protein_path": "/app/6lu7.pdb",
                "ligand_paths": ["/app/ATP.sdf"],
                "status": "success"
            }
        }
        mock_mcp_response.raise_for_status = Mock()
        mock_mcp_post.return_value = mock_mcp_response
        
        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(workflow_json)):
            parsed = parse_workflow_with_llm(llm_client, "Download 6lu7 and ATP")
        
        assert parsed["parameters"]["protein_id"] == "6lu7"
        
        # Validate
        parser = WorkflowParser()
        workflow = parser.validate_workflow(parsed)
        
        assert not parser.has_missing_parameters()
        
        # Execute (mocked)
        orchestrator = WorkflowOrchestrator(output_base="./test_output")
        results = orchestrator.execute_workflow(workflow, job_id="test_integration")
        
        assert results["job_id"] == "test_integration"
        assert "download" in results["services_executed"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
