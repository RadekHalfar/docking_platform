"""
Tests for LLM Orchestrator

Unit and integration tests for natural language workflow orchestration.
"""
import builtins
import pytest
import json
import platform
import requests
import threading
from unittest.mock import Mock, patch
from pathlib import Path

from services.llm_orchestrator import (
    WorkflowOrchestrator,
    WorkflowParser,
    WorkflowStateManager,
    create_llm_client,
    parse_workflow_with_llm,
    parse_followup_with_llm,
    extract_box_source_choice_with_llm,
)
from services.llm_orchestrator.orchestrator import docking_timeout_seconds
from services.llm_orchestrator.workflow_definition import (
    DEFAULT_PARAMETERS,
    REQUIRED_PARAMETERS_BY_STEP,
    get_service_urls_from_env,
    with_default_parameters,
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

    def test_parse_followup_with_llm_adds_ligand_onto_existing_list(self):
        """A follow-up 'add one X' should return the full merged ligand list."""
        followup_json = {
            "services_add": [],
            "parameters": {"ligand_ids": ["ATP", "ATP"]}
        }

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(followup_json)):
            result = parse_followup_with_llm(
                llm_client,
                "can you add one atp ligand?",
                current_parameters={
                    "protein_id": "6lu7",
                    "ligand_ids": ["ATP"],
                    "center": [10.0, 20.0, 30.0],
                    "box_size": [25.0, 30.0, 35.0],
                },
                current_services=["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
            )

        assert result["parameters"]["ligand_ids"] == ["ATP", "ATP"]
        assert result["services_add"] == []

    def test_parse_followup_with_llm_omits_untouched_fields(self):
        """Fields the follow-up message doesn't mention must be absent from the delta,
        so the caller's merge keeps the prior value instead of losing it."""
        followup_json = {
            "services_add": [],
            "parameters": {"ph": 6.5}
        }

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(followup_json)):
            result = parse_followup_with_llm(
                llm_client,
                "actually set pH to 6.5",
                current_parameters={
                    "protein_id": "6lu7",
                    "ligand_ids": ["ATP"],
                    "center": [10.0, 20.0, 30.0],
                    "box_size": [25.0, 30.0, 35.0],
                },
                current_services=["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
            )

        assert result["parameters"] == {"ph": 6.5}
        assert "protein_id" not in result["parameters"]
        assert "center" not in result["parameters"]
        assert "ligand_ids" not in result["parameters"]

    def test_parse_followup_with_llm_visualization_fallback(self):
        """Visualization keyword fallback should apply to follow-up turns too."""
        followup_json = {"services_add": [], "parameters": {}}

        llm_client = create_llm_client(provider="ollama", endpoint="http://test:11434", model="llama2")
        with patch.object(llm_client, "generate", return_value=json.dumps(followup_json)):
            result = parse_followup_with_llm(
                llm_client,
                "and render an image of the result",
                current_parameters={"protein_id": "6lu7", "ligand_ids": ["ATP"]},
                current_services=["get_input_files", "dock"],
            )

        assert "visualize" in result["services_add"]

    def test_chat_followup_merge_preserves_prior_params(self):
        """End-to-end: merging a follow-up delta onto prior state should keep
        already-known params, matching what ui.core.chat.parse_message does."""
        parser = WorkflowParser()
        prior = parser.validate_workflow({
            "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
            "parameters": {
                "protein_id": "6lu7",
                "ligand_ids": ["ATP"],
                "center": [10.0, 20.0, 30.0],
                "box_size": [25.0, 30.0, 35.0],
            },
        })
        assert prior["missing_required"] == []

        delta = {"services_add": [], "parameters": {"ligand_ids": ["ATP", "ATP"]}}
        services = list(dict.fromkeys(prior["services"] + delta["services_add"]))
        parameters = {**prior["parameters"], **delta["parameters"]}

        merged = parser.validate_workflow({"services": services, "parameters": parameters})

        assert merged["parameters"]["protein_id"] == "6lu7"
        assert merged["parameters"]["center"] == [10.0, 20.0, 30.0]
        assert merged["parameters"]["box_size"] == [25.0, 30.0, 35.0]
        assert merged["parameters"]["ligand_ids"] == ["ATP", "ATP"]
        assert merged["missing_required"] == []

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


def _dock_ready_session(tmp_path, job_id, ligands=("A", "B", "C"), **extra_params):
    """A session checkpointed up to prepare_protein, ready to dock *ligands*."""
    params = {
        "protein_id": "6lu7",
        "ligand_ids": list(ligands),
        "center": [0.0, 0.0, 0.0],
        "box_size": [20.0, 20.0, 20.0],
        **extra_params,
    }
    state_manager = WorkflowStateManager(tmp_path / job_id)
    state_manager.save_parameters_checkpoint(params, source="test")
    state_manager.save_execution_state_checkpoint(
        {
            "prepared_protein": "/app/orchestrator_output/x/prepared/receptor.pdbqt",
            "prepared_ligands": [f"/app/orchestrator_output/x/prepared/{l}.pdbqt" for l in ligands],
        },
        step="prepare_protein",
    )
    return state_manager, params


class TestDockingAlgorithmParameter:
    """Phase 15, Stage 15.1: docking_algorithm defaults, aliases and validation."""

    _DOCK_PARAMS = {
        "protein_id": "6lu7",
        "ligand_ids": ["ATP"],
        "center": [10, 20, 30],
        "box_size": [25, 30, 35],
    }

    def _validate(self, **extra):
        parser = WorkflowParser()
        result = parser.validate_workflow(
            {"services": ["dock"], "parameters": {**self._DOCK_PARAMS, **extra}}
        )
        return parser, result

    def test_default_is_moldina(self):
        _, result = self._validate()
        assert result["parameters"]["docking_algorithm"] == "moldina"
        assert result["missing_required"] == []

    def test_alias_is_normalised(self):
        _, result = self._validate(docking_algorithm="AutoDock Vina")
        assert result["parameters"]["docking_algorithm"] == "vina"
        assert result["missing_required"] == []

    def test_unknown_value_is_reported_with_allowed_values(self):
        parser, result = self._validate(docking_algorithm="gnina")
        assert result["missing_required"] == ["docking_algorithm"]
        assert parser.get_parameter_issue("docking_algorithm") == "invalid"
        prompt = parser.format_missing_prompt()
        assert "'gnina' is not recognised" in prompt
        assert "'moldina'" in prompt and "'vina'" in prompt

    def test_unknown_value_ignored_when_not_docking(self):
        parser = WorkflowParser()
        result = parser.validate_workflow(
            {"services": ["get_input_files"],
             "parameters": {"protein_id": "6lu7", "docking_algorithm": "gnina"}}
        )
        assert "docking_algorithm" not in result["missing_required"]

    def test_update_parameters_normalises_alias_and_clears_missing(self):
        parser, _ = self._validate(docking_algorithm="gnina")
        parser.update_parameters({"docking_algorithm": "Monte Carlo"}, persist=False)
        assert parser.parsed_workflow["parameters"]["docking_algorithm"] == "vina"
        assert parser.get_missing_parameters() == []

    def test_summary_shows_algorithm(self):
        parser, _ = self._validate(docking_algorithm="vina")
        assert "Docking Algorithm: vina" in parser.get_workflow_summary()

    @pytest.mark.parametrize("params_extra,expected", [({}, "moldina"), ({"docking_algorithm": "vina"}, "vina")])
    def test_orchestrator_passes_algorithm_to_run_docking(self, tmp_path, params_extra, expected):
        state_manager, params = _dock_ready_session(tmp_path, "algo_job", ligands=["A"], **params_extra)
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"status": "ok", "stopped": False, "successful": 1, "failed": 0,
                          "errors": [], "results": [], "docking_algorithm": expected}
        )

        orchestrator.execute_workflow(
            {"services": ["dock"], "parameters": params},
            job_id="algo_job",
            state_manager=state_manager,
        )

        tool, arguments = orchestrator.call_mcp_tool.call_args[0][1:3]
        assert tool == "run_docking"
        assert arguments["docking_algorithm"] == expected


class TestCpuParameter:
    """Review Stage R4: cpu is a whole number >= 1 once parsed, whatever the
    LLM returned, so neither the GUI form nor run_docking gets "all"."""

    def _validate(self, cpu):
        parser = WorkflowParser()
        result = parser.validate_workflow(
            {"services": ["dock"], "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"],
                                                  "center": [1, 2, 3], "box_size": [20, 20, 20], "cpu": cpu}}
        )
        return parser, result

    @pytest.mark.parametrize("cpu, expected", [(4, 4), ("4", 4), ("4.0", 4), (2.5, 2), ("all", 1), (0, 1)])
    def test_validate_makes_cpu_a_whole_number_or_the_default(self, cpu, expected):
        _, result = self._validate(cpu)
        assert result["parameters"]["cpu"] == expected

    def test_update_with_a_bad_cpu_keeps_the_current_one(self):
        parser, _ = self._validate(4)
        parser.update_parameters({"cpu": "all"}, persist=False)
        assert parser.parsed_workflow["parameters"]["cpu"] == 4
        parser.update_parameters({"cpu": "8.0"}, persist=False)
        assert parser.parsed_workflow["parameters"]["cpu"] == 8


class TestDockingTimeout:
    """Review Stage R1, Phase 15, correctness #1: the run_docking HTTP
    timeout assumed PSO's 120 s per ligand per worker, which Vina exceeds."""

    def _timeout_sent(self, tmp_path, n_ligands, **params_extra):
        state_manager, params = _dock_ready_session(
            tmp_path, "timeout_job", ligands=[f"L{i}" for i in range(n_ligands)], **params_extra,
        )
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(return_value={
            "status": "ok", "stopped": False, "successful": n_ligands, "failed": 0, "errors": [], "results": [],
        })
        orchestrator.execute_workflow({"services": ["dock"], "parameters": params},
                                      job_id="timeout_job", state_manager=state_manager)
        return orchestrator.call_mcp_tool.call_args.kwargs["timeout"]

    def test_vina_batch_gets_more_than_its_measured_run_time(self, tmp_path):
        # Measured (docs/BATCH_DOCKING.md): a 10-worker Vina wave at
        # exhaustiveness 8 takes ~144 s, so 50 ligands need ~5 x 144 s.
        timeout = self._timeout_sent(tmp_path, 50, docking_algorithm="vina", parallel=True, max_workers=10)
        assert timeout >= 5 * 144.3 * 1.5

    def test_pso_budget_unchanged(self, tmp_path):
        assert self._timeout_sent(tmp_path, 50, parallel=True, max_workers=10) == 600

    def test_vina_scales_with_exhaustiveness(self):
        assert docking_timeout_seconds(10, 1, "vina", 16) == 2 * docking_timeout_seconds(10, 1, "vina", 8)
        assert docking_timeout_seconds(10, 1, "moldina", 16) == docking_timeout_seconds(10, 1, "moldina", 8)

    def test_floor_of_300_seconds(self):
        assert docking_timeout_seconds(1, 1, "vina", 1) == 300


class TestWorkflowOrchestrator:
    """Test workflow orchestrator."""
    
    def test_init(self, tmp_path):
        """Test orchestrator initialization."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        
        assert orchestrator.output_base == tmp_path.resolve()
        assert "rcsb_downloader" in orchestrator.services
        assert "moldina_runner" in orchestrator.services

    def test_orchestrator_default_service_urls_match_workflow_definition(self, tmp_path):
        """Orchestrator should source service URLs from centralized metadata."""
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        assert orchestrator.services == get_service_urls_from_env()

    def test_orchestrator_service_urls_allow_env_override(self, monkeypatch, tmp_path):
        """Service URL env variables should override centralized defaults."""
        monkeypatch.setenv("MCP_RCSB_DOWNLOADER_URL", "http://example.local:9001")
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
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
    def test_call_mcp_tool(self, mock_post, make_mcp_response, tmp_path):
        """Test MCP tool calling."""
        mock_post.return_value = make_mcp_response(result={"success": True})

        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.current_job_id = "test_job"
        
        result = orchestrator.call_mcp_tool(
            "http://localhost:8001",
            "test_tool",
            {"param": "value"}
        )
        
        assert result["success"] is True
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_error(self, mock_post, make_mcp_response, tmp_path):
        """Test MCP tool error handling."""
        mock_post.return_value = make_mcp_response(
            error={"code": -32000, "message": "Tool failed"}
        )

        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
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


class TestWorkflowCancellation:
    """Stage 11.3: cooperative cancellation via execute_workflow(cancel_event=...)."""

    def test_no_cancel_event_behaves_exactly_as_before(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )
        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        results = orchestrator.execute_workflow(workflow, job_id="no_cancel_test")

        assert results["status"] == "completed"
        assert results["services_executed"] == ["get_input_files"]

    def test_cancel_event_already_set_skips_every_step(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
        )
        cancel_event = threading.Event()
        cancel_event.set()
        workflow = {
            "services": ["get_input_files", "prepare_ligands"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        results = orchestrator.execute_workflow(
            workflow, job_id="cancel_before_test", cancel_event=cancel_event
        )

        assert results["status"] == "cancelled"
        assert results["services_executed"] == []
        assert "Workflow cancelled by user request" in results["errors"]
        orchestrator.call_mcp_tool.assert_not_called()

    def test_cancel_event_set_mid_run_stops_before_next_step(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        cancel_event = threading.Event()

        def _fake_call(*args, **kwargs):
            # Simulate the GUI's Stop button being clicked while
            # get_input_files' MCP call is "in flight" -- by the time it
            # returns, cancellation has been requested, so prepare_ligands
            # (also requested) must never start.
            cancel_event.set()
            return {"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}

        orchestrator.call_mcp_tool = Mock(side_effect=_fake_call)
        workflow = {
            "services": ["get_input_files", "prepare_ligands"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"], "formats": {"protein": "pdb", "ligand": "sdf"}},
        }

        results = orchestrator.execute_workflow(
            workflow, job_id="cancel_mid_test", cancel_event=cancel_event
        )

        assert results["status"] == "cancelled"
        assert results["services_executed"] == ["get_input_files"]
        assert "prepare_ligands" not in results["results"]
        assert "Workflow cancelled by user request" in results["errors"]
        assert orchestrator.call_mcp_tool.call_count == 1

    def test_cancelled_run_is_logged_and_merged_to_disk(self, tmp_path):
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        cancel_event = threading.Event()
        cancel_event.set()
        workflow = {
            "services": ["get_input_files"],
            "parameters": {"protein_id": "6lu7", "ligand_ids": ["ATP"]},
        }

        orchestrator.execute_workflow(workflow, job_id="cancel_disk_test", cancel_event=cancel_event)

        results_file = tmp_path / "cancel_disk_test" / "workflow_results.json"
        saved = json.loads(results_file.read_text())
        assert saved["status"] == "cancelled"

        job_log = (tmp_path / "cancel_disk_test" / "job.log").read_text()
        assert "status=cancelled" in job_log


class TestDockingStopFlag:
    """Stage 13.1b: a stop mid-docking (moldina-runner returns "stopped")
    cancels the workflow, and a stale flag never leaks into a new dock step."""

    def test_stopped_dock_result_cancels_and_skips_visualize(self, tmp_path):
        state_manager, params = _dock_ready_session(tmp_path, "stop_dock_test")
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={
                "status": "stopped", "stopped": True, "skipped": 2,
                "successful": 1, "failed": 0, "errors": [], "results": [{"ligand_id": "A"}],
            }
        )

        results = orchestrator.execute_workflow(
            {"services": ["dock", "visualize"], "parameters": params},
            job_id="stop_dock_test",
            state_manager=state_manager,
            cancel_event=threading.Event(),
        )

        assert results["status"] == "cancelled"
        assert orchestrator.call_mcp_tool.call_count == 1  # visualize never called
        assert "dock" not in results["services_executed"]
        assert results["results"]["dock"]["stopped"] is True
        # No dock checkpoint: a later redock must dock everything again.
        assert all((e.get("details") or {}).get("step") != "dock" for e in state_manager.list_revisions())
        job_log = (tmp_path / "stop_dock_test" / "job.log").read_text()
        assert "stopped on request" in job_log

    def test_stopped_redock_does_not_merge_dock_as_executed(self, tmp_path):
        # A redock merges onto the earlier run's results on disk; the merge
        # rebuilt services_executed from the result keys, so a stopped dock
        # counted as executed next to the earlier run's (now stale)
        # visualize (Review Stage R1, Phase 13).
        state_manager, params = _dock_ready_session(tmp_path, "stop_redock_test")
        (tmp_path / "stop_redock_test" / "workflow_results.json").write_text(json.dumps({
            "status": "completed",
            "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
            "results": {s: {"old": True} for s in
                        ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]},
        }))
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(
            return_value={
                "status": "stopped", "stopped": True, "skipped": 2,
                "successful": 1, "failed": 0, "errors": [], "results": [{"ligand_id": "A"}],
            }
        )

        orchestrator.execute_workflow(
            {"services": ["dock", "visualize"], "parameters": params},
            job_id="stop_redock_test",
            state_manager=state_manager,
            cancel_event=threading.Event(),
        )

        saved = json.loads((tmp_path / "stop_redock_test" / "workflow_results.json").read_text())
        assert saved["status"] == "cancelled"
        assert saved["services_executed"] == ["get_input_files", "prepare_ligands", "prepare_protein"]
        assert saved["results"]["dock"]["stopped"] is True  # this run's partial result kept
        assert "visualize" not in saved["results"]  # the earlier run's, now stale
        assert saved["results"]["prepare_protein"] == {"old": True}

    def test_stale_stop_flag_is_cleared_before_docking(self, tmp_path):
        state_manager, params = _dock_ready_session(tmp_path, "stale_flag_test")
        docking_dir = tmp_path / "stale_flag_test" / "docking_results"
        docking_dir.mkdir(parents=True)
        (docking_dir / "STOP_REQUESTED").touch()  # left over from an earlier stopped run
        flag_present_at_call = []

        def fake_call(service_url, tool_name, arguments, timeout=300):
            flag_present_at_call.append((docking_dir / "STOP_REQUESTED").exists())
            return {"status": "ok", "stopped": False, "successful": 3, "errors": [], "results": []}

        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock(side_effect=fake_call)

        results = orchestrator.execute_workflow(
            {"services": ["dock"], "parameters": params},
            job_id="stale_flag_test",
            state_manager=state_manager,
        )

        assert flag_present_at_call == [False]
        assert results["status"] == "completed"
        assert results["services_executed"] == ["dock"]

    def test_cancel_before_dock_step_makes_no_docking_call(self, tmp_path):
        state_manager, params = _dock_ready_session(tmp_path, "cancel_pre_dock_test")
        cancel_event = threading.Event()
        cancel_event.set()
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.call_mcp_tool = Mock()

        results = orchestrator.execute_workflow(
            {"services": ["dock"], "parameters": params},
            job_id="cancel_pre_dock_test",
            state_manager=state_manager,
            cancel_event=cancel_event,
        )

        assert results["status"] == "cancelled"
        orchestrator.call_mcp_tool.assert_not_called()


class TestWorkflowOrchestratorErrors:
    """Tests for WorkflowOrchestrator error handling (A2)."""

    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_raises_on_http_error(self, mock_post, tmp_path):
        """HTTP-level errors from a service should be re-raised as RuntimeError."""
        mock_post.side_effect = requests.HTTPError("503 Service Unavailable")

        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        orchestrator.current_job_id = "err_test"

        with pytest.raises(RuntimeError, match="download_structures"):
            orchestrator.call_mcp_tool("http://localhost:8001", "download_structures", {})

    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_call_mcp_tool_raises_on_connection_error(self, mock_post, tmp_path):
        """Connection-level failures should surface as RuntimeError."""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
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

        assert manager.load_latest_parameters() == with_default_parameters(
            {"protein_id": "6lu7", "ligand_ids": ["ATP"]}
        )
        assert manager.load_latest_state() == {
            "protein_file": "/app/6lu7.pdb",
            "ligand_files": ["/app/ATP.sdf"],
        }


class TestIntegration:
    """Integration tests for full workflow."""
    
    @patch('services.llm_orchestrator.orchestrator.requests.Session.post')
    def test_full_workflow_mock(self, mock_mcp_post, make_mcp_response, tmp_path):
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
        orchestrator = WorkflowOrchestrator(output_base=str(tmp_path))
        results = orchestrator.execute_workflow(workflow, job_id="test_integration")
        
        assert results["job_id"] == "test_integration"
        assert "get_input_files" in results["services_executed"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
