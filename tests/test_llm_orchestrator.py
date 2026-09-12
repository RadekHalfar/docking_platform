"""
Tests for LLM Orchestrator

Unit and integration tests for natural language workflow orchestration.
"""
import builtins
import pytest
import json
import platform
import requests
from unittest.mock import Mock, patch
from pathlib import Path

from services.llm_orchestrator import (
    WorkflowOrchestrator,
    WorkflowParser,
    WorkflowStateManager,
    create_llm_client,
    parse_workflow_with_llm,
    extract_box_source_choice_with_llm,
)
from services.llm_orchestrator.workflow_definition import (
    DEFAULT_PARAMETERS,
    REQUIRED_PARAMETERS_BY_STEP,
    get_service_urls_from_env,
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
            "services": ["get_input_files", "dock"],
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

        assert result["services"] == ["get_input_files", "dock"]
        assert result["parameters"]["protein_id"] == "6lu7"
        assert "center" in result["parameters"]

    def test_parse_workflow_with_llm_extracts_ligand_folder(self):
        """LLM-parsed JSON carrying ligand_folder (no ligand_ids) should pass through
        unchanged, since the LLM is responsible for choosing one or the other."""
        workflow_json = {
            "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_folder": "/data/ligands",
                "center": [10, 20, 30],
                "box_size": [25, 30, 35]
            },
            "missing_required": []
        }

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(workflow_json)):
            result = parse_workflow_with_llm(
                llm_client, "Dock protein 6lu7 against all ligands in /data/ligands"
            )

        assert result["parameters"]["ligand_folder"] == "/data/ligands"
        assert "ligand_ids" not in result["parameters"]

    def test_parse_workflow_visualization_fallback(self):
        """Visualization keyword fallback should append visualize service."""
        llm_output = {
            "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
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

    def test_extract_box_source_choice_with_llm(self):
        """LLM classifier should map responses to manual/pymol choices."""
        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")

        with patch.object(llm_client, "generate", return_value='{"box_source": "pymol"}'):
            assert extract_box_source_choice_with_llm(llm_client, "use interactive pymol") == "pymol"

        with patch.object(llm_client, "generate", return_value='{"box_source": "manual"}'):
            assert extract_box_source_choice_with_llm(llm_client, "I will type coordinates") == "manual"

        with patch.object(llm_client, "generate", return_value='{"box_source": "unknown"}'):
            assert extract_box_source_choice_with_llm(llm_client, "not sure") is None


class TestWorkflowParser:
    """Test workflow parser and validator."""
    
    def test_validate_workflow_basic(self):
        """Test basic workflow validation."""
        parser = WorkflowParser()
        
        workflow = {
            "services": ["get_input_files"],
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
        assert result["parameters"]["cpu"] == 1
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

    def test_invalid_protein_id_is_treated_as_missing(self):
        """Malformed protein IDs should trigger clarification instead of execution."""
        parser = WorkflowParser()

        workflow = {
            "services": ["get_input_files", "prepare_protein", "dock"],
            "parameters": {
                "protein_id": "6lu",
                "ligand_ids": ["ATP"],
                "center": [10, 20, 30],
                "box_size": [25, 30, 35],
            },
        }

        result = parser.validate_workflow(workflow)

        assert "protein_id" in result["missing_required"]

    def test_invalid_protein_id_prompt_says_invalid_not_missing(self):
        """Clarification text should describe invalid protein ID explicitly."""
        parser = WorkflowParser()

        workflow = {
            "services": ["get_input_files"],
            "parameters": {
                "protein_id": "6lu"
            }
        }

        parser.validate_workflow(workflow)
        prompt = parser.format_missing_prompt().lower()

        assert "protein pdb id is invalid" in prompt
        assert "missing required parameters" not in prompt
    
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

    def test_update_parameters_persists_when_state_manager_attached(self):
        """Parameter updates should be checkpointed when persistence is configured."""
        state_manager = Mock()
        parser = WorkflowParser(state_manager=state_manager)

        parser.parsed_workflow = {
            "services": ["dock"],
            "parameters": {"protein_id": "6lu7"},
            "missing_required": ["ligand_ids"],
        }

        parser.update_parameters({"ligand_ids": ["ATP"]}, source="user_clarification")

        state_manager.save_parameters_checkpoint.assert_called_once_with(
            parser.parsed_workflow["parameters"],
            source="user_clarification",
        )
    
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
            "services": ["get_input_files", "dock"],
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

    def test_parser_uses_central_required_parameter_contract(self):
        """Missing dock parameters should follow centralized requirements."""
        parser = WorkflowParser()

        result = parser.validate_workflow(
            {
                "services": ["dock"],
                "parameters": {
                    "protein_id": "6lu7",
                    "ligand_ids": ["ATP"],
                },
            }
        )

        expected_missing = {
            p
            for p in REQUIRED_PARAMETERS_BY_STEP["dock"]
            if p not in {"protein_id", "ligand_ids"}
        }
        assert expected_missing.issubset(set(result["missing_required"]))

    def test_update_parameters_clears_multiple_missing_at_once(self):
        """Updating several missing params in one call should clear all of them (M4 list comprehension)."""
        parser = WorkflowParser()
        parser.parsed_workflow = {
            "services": ["dock"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
            "missing_required": ["center", "box_size"],
        }

        parser.update_parameters({"center": [10.0, 20.0, 30.0], "box_size": [25.0, 25.0, 25.0]})

        missing = parser.get_missing_parameters()
        assert "center" not in missing
        assert "box_size" not in missing

    def test_update_parameters_ignores_keys_not_in_missing(self):
        """Updating a key not listed in missing_required should leave the list unchanged."""
        parser = WorkflowParser()
        parser.parsed_workflow = {
            "services": ["dock"],
            "parameters": {"protein_id": "6lu7"},
            "missing_required": ["center"],
        }

        parser.update_parameters({"ph": 7.4})

        assert "center" in parser.get_missing_parameters()

    def test_parser_applies_central_default_parameters(self):
        """Parser defaults should stay aligned with centralized defaults."""
        parser = WorkflowParser()
        result = parser.validate_workflow(
            {
                "services": ["get_input_files"],
                "parameters": {"protein_id": "6lu7"},
            }
        )

        assert result["parameters"]["ph"] == DEFAULT_PARAMETERS["ph"]
        assert result["parameters"]["cpu"] == DEFAULT_PARAMETERS["cpu"]


class TestWorkflowOrchestrator:
    """Test workflow orchestrator."""
    
    def test_init(self):
        """Test orchestrator initialization."""
        orchestrator = WorkflowOrchestrator(output_base="./test_output")
        
        assert orchestrator.output_base == Path("./test_output").resolve()
        assert "rcsb_downloader" in orchestrator.services
        assert "moldina_runner" in orchestrator.services

    def test_orchestrator_default_service_urls_match_workflow_definition(self):
        """Orchestrator should source service URLs from centralized metadata."""
        orchestrator = WorkflowOrchestrator(output_base="./test_output")
        assert orchestrator.services == get_service_urls_from_env()

    def test_orchestrator_service_urls_allow_env_override(self, monkeypatch):
        """Service URL env variables should override centralized defaults."""
        monkeypatch.setenv("MCP_RCSB_DOWNLOADER_URL", "http://example.local:9001")
        orchestrator = WorkflowOrchestrator(output_base="./test_output")
        assert orchestrator.services["rcsb_downloader"] == "http://example.local:9001"
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.get')
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
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool(self, mock_post, make_mcp_response):
        """Test MCP tool calling."""
        mock_post.return_value = make_mcp_response(result={"success": True})
        
        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "test_job"
        
        result = orchestrator.call_mcp_tool(
            "http://localhost:8001",
            "test_tool",
            {"param": "value"}
        )
        
        assert result["success"] is True
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_error(self, mock_post, make_mcp_response):
        """Test MCP tool error handling."""
        mock_post.return_value = make_mcp_response(
            error={"code": -32000, "message": "Tool failed"}
        )
        
        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "test_job"
        
        with pytest.raises(RuntimeError, match="Tool failed"):
            orchestrator.call_mcp_tool(
                "http://localhost:8001",
                "test_tool",
                {"param": "value"}
            )

    def test_execute_workflow_prefers_latest_disk_parameters(self, tmp_path):
        """Before each step, orchestrator should reload latest parameters from disk."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        state_manager = WorkflowStateManager(tmp_path / "job_latest_params")

        state_manager.save_parameters_checkpoint(
            {
                "protein_id": "1abc",
                "ligand_ids": ["HEM"],
                "formats": {"protein": "pdb", "ligand": "sdf"},
            },
            source="manual_override",
        )

        captured_arguments = {}

        def _fake_call(service_url, tool_name, arguments):
            captured_arguments.update(arguments)
            return {
                "protein_path": "/app/1abc.pdb",
                "ligand_paths": ["/app/HEM.sdf"],
            }

        orchestrator.call_mcp_tool = Mock(side_effect=_fake_call)

        workflow = {
            "services": ["get_input_files"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
                "formats": {"protein": "pdb", "ligand": "sdf"},
            },
        }

        results = orchestrator.execute_workflow(
            workflow,
            job_id="job_reload_params",
            state_manager=state_manager,
        )

        assert results["status"] == "completed"
        assert captured_arguments["protein_id"] == "1abc"
        assert captured_arguments["ligand_ids"] == ["HEM"]


class TestJobLog:
    """Stage 11.1: job.log narration written by execute_workflow()."""

    def _job_log_text(self, tmp_path, job_id):
        log_path = tmp_path / job_id / "job.log"
        assert log_path.exists(), "job.log should have been created"
        return log_path.read_text(encoding="utf-8")

    def test_execute_workflow_logs_start_step_and_closing_lines(self, tmp_path, make_mcp_response):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )

        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        orchestrator.execute_workflow(workflow, job_id="log_test", job_name="My Run")

        text = self._job_log_text(tmp_path, "log_test")
        assert "Starting workflow execution: job_id=log_test job_name=My Run" in text
        assert "get_input_files completed in" in text
        assert "Workflow finished: job_id=log_test status=completed" in text

    def test_execute_workflow_logs_full_traceback_on_failure(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(side_effect=RuntimeError("service down"))

        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": [], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        orchestrator.execute_workflow(workflow, job_id="traceback_test")

        text = self._job_log_text(tmp_path, "traceback_test")
        assert "Traceback (most recent call last)" in text
        assert "RuntimeError: service down" in text
        assert "Workflow finished: job_id=traceback_test status=failed" in text

    def test_call_mcp_tool_logs_one_line_per_call(self, tmp_path, make_mcp_response):
        with patch("services.llm_orchestrator.orchestrator.requests.Session.post") as mock_post:
            mock_post.return_value = make_mcp_response(result={"ok": True})

            orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
            orchestrator.current_job_id = "mcp_call_log_test"

            orchestrator.call_mcp_tool("http://localhost:8001", "test_tool", {"param": "value"})

        text = self._job_log_text(tmp_path, "mcp_call_log_test")
        lines = [line for line in text.splitlines() if "test_tool" in line]
        assert len(lines) == 1
        assert "completed in" in lines[0]


class TestWorkflowResultsMerge:
    """Stage 11.1: workflow_results.json accumulates across execute_workflow() calls."""

    def test_second_call_with_different_services_preserves_first_calls_results(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        state_manager = WorkflowStateManager(tmp_path / "merge_test")

        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )
        first_workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }
        first_results = orchestrator.execute_workflow(
            first_workflow, job_id="merge_test", state_manager=state_manager, job_name="Run 1"
        )
        assert first_results["services_executed"] == ["get_input_files"]

        orchestrator.call_mcp_tool = Mock(
            return_value={"prepared_ligands": [{"pdbqt_path": "/app/prepared/ATP.pdbqt"}]}
        )
        second_workflow = {
            "services": ["prepare_ligands"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
        }
        # Reuses the same job_dir's persisted workflow_state (get_input_files'
        # ligand_files) via state_manager, exactly like a GUI/CLI redock would.
        second_results = orchestrator.execute_workflow(
            second_workflow, job_id="merge_test", state_manager=state_manager
        )

        results_file = tmp_path / "merge_test" / "workflow_results.json"
        saved = json.loads(results_file.read_text())

        assert "get_input_files" in saved["results"]
        assert "prepare_ligands" in saved["results"]
        assert saved["services_executed"] == ["get_input_files", "prepare_ligands"]
        # Earlier non-empty job_name survives a second call that didn't supply one.
        assert saved["job_name"] == "Run 1"
        # The returned dict (not just the on-disk file) reflects the merge too.
        assert second_results["services_executed"] == ["get_input_files", "prepare_ligands"]

    def test_repeated_step_lets_the_new_call_win(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))

        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )
        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }
        orchestrator.execute_workflow(workflow, job_id="rewin_test")

        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/1abc.pdb", "ligand_paths": ["/app/HEM.sdf"]}
        )
        workflow2 = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "1abc", "ligand_ids": ["HEM"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }
        orchestrator.execute_workflow(workflow2, job_id="rewin_test")

        results_file = tmp_path / "rewin_test" / "workflow_results.json"
        saved = json.loads(results_file.read_text())

        assert saved["results"]["get_input_files"]["protein_path"] == "/app/1abc.pdb"

    def test_fresh_job_dir_is_unaffected_by_merge_logic(self, tmp_path):
        """A single execute_workflow() call against a brand-new job_dir must
        behave exactly as before (no pre-existing file to merge with)."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )
        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        results = orchestrator.execute_workflow(workflow, job_id="fresh_test")

        assert results["services_executed"] == ["get_input_files"]
        results_file = tmp_path / "fresh_test" / "workflow_results.json"
        saved = json.loads(results_file.read_text())
        assert saved["services_executed"] == ["get_input_files"]


class TestWorkflowOrchestratorErrors:
    """Tests for WorkflowOrchestrator error handling (A2)."""

    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_raises_on_http_error(self, mock_post):
        """HTTP-level errors from a service should be re-raised as RuntimeError."""
        mock_post.side_effect = requests.HTTPError("503 Service Unavailable")

        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "err_test"

        with pytest.raises(RuntimeError, match="download_structures"):
            orchestrator.call_mcp_tool("http://localhost:8001", "download_structures", {})

    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_raises_on_connection_error(self, mock_post):
        """Connection-level failures should surface as RuntimeError."""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        orchestrator = WorkflowOrchestrator()
        orchestrator.current_job_id = "conn_test"

        with pytest.raises(RuntimeError):
            orchestrator.call_mcp_tool("http://localhost:8001", "download_structures", {})

    def test_execute_workflow_status_failed_on_step_error(self, tmp_path):
        """A failing step should mark the workflow as 'failed' and record the error."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(side_effect=RuntimeError("service down"))

        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": [], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        results = orchestrator.execute_workflow(workflow, job_id="fail_test")

        assert results["status"] == "failed"
        assert len(results["errors"]) == 1
        assert "service down" in results["errors"][0]

    def test_execute_workflow_returns_results_despite_ioerror_on_save(self, tmp_path):
        """IOError when writing workflow_results.json must not propagate (M6 try/except)."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": []}
        )

        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": [], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        original_open = builtins.open

        def fail_on_results_write(path, mode="r", *args, **kwargs):
            if "workflow_results.json" in str(path) and ("w" in mode or "a" in mode):
                raise IOError("disk full")
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=fail_on_results_write):
            results = orchestrator.execute_workflow(workflow, job_id="io_err_test")

        assert isinstance(results, dict)
        assert results["job_id"] == "io_err_test"
        assert "get_input_files" in results["services_executed"]

    def test_execute_workflow_skips_step_when_not_in_services(self, tmp_path):
        """Services not listed in the workflow should never be called."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        called_tools = []

        def recording_call(service_url, tool_name, arguments):
            called_tools.append(tool_name)
            return {"protein_path": "/app/6lu7.pdb", "ligand_paths": []}

        orchestrator.call_mcp_tool = Mock(side_effect=recording_call)

        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": [], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        orchestrator.execute_workflow(workflow, job_id="skip_test")

        assert "visualize_docking" not in called_tools
        assert "prepare_ligands" not in called_tools
        assert "download_structures" in called_tools


class TestWorkflowStateManager:
    """Tests for hybrid latest+revision checkpoint persistence."""

    def test_saves_revisioned_checkpoints_and_latest_files(self, tmp_path):
        manager = WorkflowStateManager(tmp_path / "job_state_manager")

        rev1 = manager.save_parameters_checkpoint({"protein_id": "6lu7"}, source="initial_parse")
        rev2 = manager.save_parameters_checkpoint(
            {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
            source="user_clarification",
        )
        rev3 = manager.save_execution_state_checkpoint(
            {"protein_file": "/app/6lu7.pdb", "ligand_files": ["/app/ATP.sdf"]},
            step="get_input_files",
        )

        assert rev1 == 1
        assert rev2 == 2
        assert rev3 == 3
        assert manager.get_current_revision() == 3

        assert (manager.checkpoints_dir / "rev_000001_parameters.json").exists()
        assert (manager.checkpoints_dir / "rev_000002_parameters.json").exists()
        assert (manager.checkpoints_dir / "rev_000003_state.json").exists()

        assert manager.load_latest_parameters() == {"protein_id": "6lu7", "ligand_ids": ["ATP"]}
        assert manager.load_latest_state() == {
            "protein_file": "/app/6lu7.pdb",
            "ligand_files": ["/app/ATP.sdf"],
        }


class TestIntegration:
    """Integration tests for full workflow."""
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_full_workflow_mock(self, mock_mcp_post, make_mcp_response):
        """Test full workflow with mocked services."""
        workflow_json = {
            "services": ["get_input_files"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"]
            },
            "missing_required": []
        }
        
        mock_mcp_post.return_value = make_mcp_response(
            result={
                "protein_path": "/app/6lu7.pdb",
                "ligand_paths": ["/app/ATP.sdf"],
                "status": "success",
            }
        )
        
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
        assert "get_input_files" in results["services_executed"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
