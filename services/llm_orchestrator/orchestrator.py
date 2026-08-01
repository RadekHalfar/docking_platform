"""
Workflow Orchestrator

Coordinates execution of molecular docking workflow across MCP services.
"""
import copy
import json
import logging
import math
import shutil
import time
import uuid
import requests
from typing import Dict, Any, List, Optional
from pathlib import Path

from common.file_utils import scan_ligand_folder
from common.http_retry import build_mcp_retry_session
from common.path_utils import to_container_path
from services.llm_orchestrator.workflow_definition import get_service_urls_from_env

logger = logging.getLogger(__name__)

# Retries only connection-establishment failures, never read timeouts/5xx --
# see common/http_retry.py for why that distinction matters for a call that
# can trigger a real side-effecting action (e.g. run_docking).
_mcp_session = build_mcp_retry_session()

# Verbosity levels (match nlp_orchestrator.py)
VERBOSITY_LOW = 0       # Minimal output (step summaries only)
VERBOSITY_NORMAL = 1    # Normal output (current behavior)
VERBOSITY_MCP_DEMO = 2  # Full MCP demonstration mode (show JSON-RPC messages)


class WorkflowOrchestrator:
    """Orchestrates workflow execution across MCP services."""
    
    def __init__(self, output_base: str = "./orchestrator_output", verbosity: int = VERBOSITY_NORMAL):
        """
        Initialize orchestrator.
        
        Args:
            output_base: Base directory for workflow outputs
            verbosity: Verbosity level (VERBOSITY_LOW, VERBOSITY_NORMAL, VERBOSITY_MCP_DEMO)
        """
        self.output_base = Path(output_base).resolve()
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        # Store the base directory for path conversion
        self.workspace_root = Path.cwd().resolve()
        
        # Verbosity level
        self.verbosity = verbosity

        # MCP service URLs (env-overridable for Docker/container usage)
        self.services = get_service_urls_from_env()

        # Workflow state
        self.workflow_state = {}
        self.current_job_id = None

    def _step_print(self, message: str) -> None:
        """Print a step-level message to stdout and always record it via the logger."""
        logger.info(message)
        if self.verbosity in (VERBOSITY_LOW, VERBOSITY_MCP_DEMO):
            print(message)
    
    def check_services_health(self) -> Dict[str, bool]:
        """Check health of all MCP services."""
        health = {}
        for name, url in self.services.items():
            try:
                response = _mcp_session.get(f"{url}/health", timeout=5)
                health[name] = response.status_code == 200
            except requests.RequestException:
                health[name] = False
        return health
    
    def call_mcp_tool(self, service_url: str, tool_name: str, arguments: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
        """
        Call an MCP tool via JSON-RPC 2.0.

        Args:
            service_url: URL of the MCP service
            tool_name: Name of the tool to call
            arguments: Tool arguments
            timeout: HTTP request timeout in seconds (default: 300)

        Returns:
            Tool result
        """
        # Generate unique ID for each request (required by JSON-RPC 2.0)
        request_id = f"orchestrator_{self.current_job_id}_{str(uuid.uuid4())[:8]}"
        
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tool_call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        # MCP Demo mode: Show full JSON-RPC communication
        if self.verbosity == VERBOSITY_MCP_DEMO:
            print("\n" + "." * 80)
            print(f"📡 MCP Client → Server ({service_url})")
            print("." * 80)
            print()
            print("JSON-RPC:")
            print(json.dumps(payload, indent=2))
            print("─" * 80)
        
        logger.info(f"Calling {tool_name} on {service_url}")
        # Only log arguments in normal verbosity (not in MCP demo mode where we already show full payload)
        if self.verbosity != VERBOSITY_MCP_DEMO:
            logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        try:
            response = _mcp_session.post(f"{service_url}/mcp", json=payload, timeout=timeout)
            response.raise_for_status()
            
            data = response.json()
            
            # MCP Demo mode: Show response
            if self.verbosity == VERBOSITY_MCP_DEMO:
                print("." * 80)
                print(f"📡 MCP Server ({service_url}) → Client")
                print("." * 80)
                print()
                print("JSON-RPC:")
                print(json.dumps(data, indent=2))
                print("─" * 80 + "\n")
            
            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                logger.error(f"MCP Error: {error_msg}")
                raise RuntimeError(f"MCP Error: {error_msg}")
            
            return data.get("result", {})
            
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise RuntimeError(f"Failed to call {tool_name}: {e}")
    
    def _copy_local_input(self, source_path: str, dest_dir: Path) -> str:
        """Copy a user-supplied local file into the job's downloads dir and return its container path."""
        src = Path(source_path)
        if not src.is_file():
            raise RuntimeError(f"Local input file not found: {source_path}")
        dest = dest_dir / src.name
        shutil.copy(src, dest)
        return self.normalize_path(str(dest))

    def normalize_path(self, path: str) -> str:
        """
        Convert path to Docker container-compatible format.
        
        Converts absolute Windows paths to container mount paths:
        - C:/Users/.../docking-system/orchestrator_output/... -> /app/orchestrator_output/...
        - C:/Users/.../docking-system/demo_output/... -> /app/demo_output/...
        """
        return to_container_path(path=path, workspace_root=self.workspace_root, container_root="/app")
    
    def execute_workflow(
        self,
        workflow: Dict[str, Any],
        job_id: str = None,
        state_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute a complete workflow.
        
        Args:
            workflow: Validated workflow dictionary
            job_id: Optional job identifier
            state_manager: Optional persistence manager used for checkpointing
            
        Returns:
            Workflow execution results
        """
        # Reset transient per-run state so artifacts from previous runs are not reused.
        self.workflow_state = {}

        if job_id:
            self.current_job_id = job_id
        else:
            self.current_job_id = f"job_{int(time.time())}"
        
        services = workflow.get("services", [])
        params = workflow.get("parameters", {})

        def _reload_latest_from_disk() -> None:
            nonlocal params
            if not state_manager:
                return

            latest_params = state_manager.load_latest_parameters()
            if isinstance(latest_params, dict):
                params = latest_params

            latest_state = state_manager.load_latest_state()
            if isinstance(latest_state, dict):
                self.workflow_state = copy.deepcopy(latest_state)

        def _save_step_checkpoint(step: str) -> None:
            if state_manager:
                state_manager.save_execution_state_checkpoint(self.workflow_state, step=step)
        
        # Create job directory
        job_dir = self.output_base / self.current_job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        results = {
            "job_id": self.current_job_id,
            "services_executed": [],
            "results": {},
            "errors": []
        }
        
        logger.info(f"Starting workflow execution: {self.current_job_id}")
        logger.info(f"Services to execute: {services}")
        
        try:
            # Step 1: Get input files (RCSB download and/or local file copy)
            if "get_input_files" in services:
                _reload_latest_from_disk()

                self._step_print("\n\U0001f4e5 STEP 1: Getting input files...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 1: Getting input files")
                    logger.info("="*80)
                
                download_dir = job_dir / "downloads"
                download_dir.mkdir(exist_ok=True)

                local_protein_file = params.get("protein_file")

                ligand_folder = params.get("ligand_folder")
                if ligand_folder:
                    # A ligand folder supersedes individual ligand_files/ligand_ids:
                    # every matching file in the folder is docked, nothing else.
                    local_ligand_files = scan_ligand_folder(ligand_folder)
                    if not local_ligand_files:
                        raise ValueError(f"No ligand files found in {ligand_folder}")
                    self._step_print(
                        f"   \U0001f4c1 Found {len(local_ligand_files)} ligand file(s) in {ligand_folder}"
                    )
                    remote_ligand_ids: List[str] = []
                else:
                    local_ligand_files = params.get("ligand_files") or []
                    remote_ligand_ids = params.get("ligand_ids") or []

                needs_remote_protein = not local_protein_file
                needs_remote_ligands = bool(remote_ligand_ids)

                download_result: Dict[str, Any] = {}
                protein_path = None
                downloaded_ligand_paths: List[str] = []

                # Local ligand files and RCSB ligand IDs can be combined: each side is
                # fetched independently and the resulting paths are merged below.
                if needs_remote_protein or needs_remote_ligands:
                    download_result = self.call_mcp_tool(
                        self.services["rcsb_downloader"],
                        "download_structures",
                        {
                            "protein_id": params.get("protein_id") if needs_remote_protein else None,
                            "ligand_ids": remote_ligand_ids if needs_remote_ligands else [],
                            "enable_name_resolution": False,
                            "formats": params.get("formats", {"protein": "pdb", "ligand": "sdf"}),
                            "save_dir": self.normalize_path(download_dir)
                        }
                    )
                    if needs_remote_protein:
                        protein_path = download_result.get("protein_path")
                    if needs_remote_ligands:
                        downloaded_ligand_paths = download_result.get("ligand_paths", [])

                if local_protein_file:
                    protein_path = self._copy_local_input(local_protein_file, download_dir)

                local_ligand_paths = [self._copy_local_input(f, download_dir) for f in local_ligand_files]
                ligand_paths = downloaded_ligand_paths + local_ligand_paths

                results["results"]["get_input_files"] = {
                    **download_result,
                    "protein_path": protein_path,
                    "ligand_paths": ligand_paths,
                    "status": download_result.get("status", "ok"),
                }
                results["services_executed"].append("get_input_files")

                self.workflow_state["protein_file"] = protein_path
                self.workflow_state["ligand_files"] = ligand_paths

                protein_source = "local file" if local_protein_file else "RCSB download"
                if local_ligand_files and remote_ligand_ids:
                    ligand_source = "local files + RCSB download"
                elif local_ligand_files:
                    ligand_source = "local files"
                else:
                    ligand_source = "RCSB download"
                self._step_print(
                    f"   \u2705 Got protein ({protein_source}) and "
                    f"{len(self.workflow_state['ligand_files'])} ligand(s) ({ligand_source})"
                )
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info(f"\u2705 Got protein ({protein_source}): {self.workflow_state['protein_file']}")
                    logger.info(f"\u2705 Got {len(self.workflow_state['ligand_files'])} ligand(s) ({ligand_source})")

                _save_step_checkpoint("get_input_files")
            
            # Step 2: Prepare ligands
            if "prepare_ligands" in services or "prepare_protein" in services:
                prepared_dir = job_dir / "prepared"
                prepared_dir.mkdir(exist_ok=True)
            
            if "prepare_ligands" in services:
                _reload_latest_from_disk()

                self._step_print("\n\U0001f9ea STEP 2: Preparing ligands...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 2: Preparing ligands")
                    logger.info("="*80)
                
                if not self.workflow_state.get("ligand_files"):
                    raise RuntimeError("No ligand files available for preparation")
                
                prepare_ligands_result = self.call_mcp_tool(
                    self.services["ligand_preparer"],
                    "prepare_ligands",
                    {
                        "ligand_paths": self.workflow_state["ligand_files"],  # Already in container format
                        "output_dir": self.normalize_path(prepared_dir),
                        "ph": params.get("ph", 7.4),
                        "add_hydrogens": params.get("add_hydrogens", True),
                        "generate_3d": params.get("generate_3d", True),
                        "optimize": params.get("optimize", True)
                    }
                )
                
                results["results"]["prepare_ligands"] = prepare_ligands_result
                results["services_executed"].append("prepare_ligands")
                
                # Store prepared ligand paths
                prepared_ligands = []
                for result in prepare_ligands_result.get("prepared_ligands", []):
                    if result.get("pdbqt_path"):
                        prepared_ligands.append(result["pdbqt_path"])
                
                self.workflow_state["prepared_ligands"] = prepared_ligands
                self._step_print(f"   \u2705 Prepared {len(prepared_ligands)} ligand(s)")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info(f"\u2705 Prepared {len(prepared_ligands)} ligands")

                _save_step_checkpoint("prepare_ligands")
            
            # Step 3: Prepare protein
            if "prepare_protein" in services:
                _reload_latest_from_disk()

                self._step_print("\n\U0001f9ec STEP 3: Preparing protein...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 3: Preparing protein")
                    logger.info("="*80)
                
                if not self.workflow_state.get("protein_file"):
                    raise RuntimeError("No protein file available for preparation")
                
                prepare_protein_result = self.call_mcp_tool(
                    self.services["protein_preparer"],
                    "prepare_proteins",
                    {
                        "protein_paths": [self.workflow_state["protein_file"]],  # Already in container format
                        "output_dir": self.normalize_path(prepared_dir),
                        "ph": params.get("ph", 7.4),
                        "add_hydrogens": params.get("add_hydrogens", True),
                        "remove_waters": params.get("remove_waters", True)
                    }
                )
                
                results["results"]["prepare_protein"] = prepare_protein_result
                results["services_executed"].append("prepare_protein")
                
                # Store prepared protein path
                protein_results = prepare_protein_result.get("prepared_proteins", [])
                if protein_results and protein_results[0].get("pdbqt_path"):
                    self.workflow_state["prepared_protein"] = protein_results[0].get("pdbqt_path")
                    self._step_print("   \u2705 Protein prepared successfully")
                    if self.verbosity == VERBOSITY_NORMAL:
                        logger.info(f"\u2705 Prepared protein: {self.workflow_state['prepared_protein']}")

                _save_step_checkpoint("prepare_protein")
            
            # Step 4: Run docking
            if "dock" in services:
                _reload_latest_from_disk()

                self._step_print("\n\u26db\ufe0f  STEP 4: Running molecular docking...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 4: Running molecular docking")
                    logger.info("="*80)
                
                if not self.workflow_state.get("prepared_protein"):
                    raise RuntimeError("No prepared protein available for docking")
                if not self.workflow_state.get("prepared_ligands"):
                    raise RuntimeError("No prepared ligands available for docking")
                
                docking_dir = job_dir / "docking_results"
                docking_dir.mkdir(exist_ok=True)
                
                if self.verbosity != VERBOSITY_LOW:
                    logger.info(f"Docking parameters:")
                    logger.info(f"  Center: {params['center']}")
                    logger.info(f"  Box Size: {params['box_size']}")
                    logger.info(f"  Exhaustiveness: {params.get('exhaustiveness', 8)}")
                    logger.info(f"  Population: {params.get('population', 500)}")
                
                # Moldina docks ligands one subprocess at a time by default (or up to
                # max_workers concurrently when parallel is enabled), so the HTTP
                # timeout must scale with batch size / parallelism — a flat 300s cap
                # silently fails any sequential batch of more than a couple ligands.
                parallel = bool(params.get("parallel", False))
                max_workers = max(1, int(params.get("max_workers", 1)))
                effective_workers = max_workers if parallel else 1
                dock_timeout = max(
                    300,
                    math.ceil(120 * len(self.workflow_state["prepared_ligands"]) / effective_workers),
                )

                docking_result = self.call_mcp_tool(
                    self.services["moldina_runner"],
                    "run_docking",
                    {
                        "receptor_path": self.workflow_state["prepared_protein"],  # Already in container format
                        "ligand_paths": self.workflow_state["prepared_ligands"],  # Already in container format
                        "output_dir": self.normalize_path(docking_dir),
                        "center": params["center"],
                        "box_size": params["box_size"],
                        "cpu": params.get("cpu", 1),
                        "population": params.get("population", 500),
                        "exhaustiveness": params.get("exhaustiveness", 8),
                        "num_modes": params.get("num_modes", 15),
                        "energy_range": params.get("energy_range", 100.0),
                        "parallel": parallel,
                        "max_workers": max_workers,
                    },
                    timeout=dock_timeout,
                )
                
                results["results"]["dock"] = docking_result
                results["services_executed"].append("dock")
                
                # Store docking results
                self.workflow_state["docking_results"] = docking_result
                self._step_print(f"   \u2705 Docking completed: {docking_result.get('successful', 0)} successful")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info(f"\u2705 Docking completed: {docking_result.get('successful', 0)} successful")

                _save_step_checkpoint("dock")
            
            # Step 5: Visualize
            if "visualize" in services:
                _reload_latest_from_disk()

                self._step_print("\n\U0001f3a8 STEP 5: Generating visualization...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 5: Generating visualization")
                    logger.info("="*80)
                
                if not self.workflow_state.get("prepared_protein"):
                    raise RuntimeError("No prepared protein available for visualization")
                
                # Get docked ligands
                docked_ligands = []
                affinities = []
                
                if self.workflow_state.get("docking_results"):
                    for ligand_result in self.workflow_state["docking_results"].get("results", []):
                        if ligand_result.get("output_path"):
                            docked_ligands.append(ligand_result["output_path"])
                            # None (not 0.0) when a ligand produced no parseable score —
                            # defaulting to 0.0 would make an unscored ligand look like
                            # the best binder in the batch.
                            affinities.append(ligand_result.get("best_affinity"))
                
                if not docked_ligands:
                    if self.verbosity != VERBOSITY_LOW:
                        logger.warning("No docked ligands found, using prepared ligands for visualization")
                    docked_ligands = self.workflow_state.get("prepared_ligands", [])
                    affinities = None
                
                viz_dir = job_dir / "visualization"
                viz_dir.mkdir(exist_ok=True)
                
                viz_result = self.call_mcp_tool(
                    self.services["pymol_visualizer"],
                    "visualize_docking",
                    {
                        "receptor_path": self.workflow_state["prepared_protein"],  # Already in container format
                        "ligand_paths": docked_ligands,  # Already in container format
                        "output_dir": self.normalize_path(viz_dir),
                        "affinities": affinities,
                        "show_surface": True,
                        "show_binding_site": True,
                        "binding_site_radius": 5.0,
                        "render_image": True,
                        "image_width": 1920,
                        "image_height": 1080,
                        "image_dpi": 300,
                        "ray_trace": True
                    }
                )
                
                results["results"]["visualize"] = viz_result
                results["services_executed"].append("visualize")
                
                self._step_print("   \u2705 Visualization created")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info(f"\u2705 Visualization created: {viz_result.get('script_path')}")
                    if viz_result.get("image_path"):
                        logger.info(f"\u2705 Image rendered: {viz_result.get('image_path')}")

                _save_step_checkpoint("visualize")
            
            # Success
            results["status"] = "completed"
            self._step_print("\n\u2705 WORKFLOW COMPLETED SUCCESSFULLY\n")
            if self.verbosity == VERBOSITY_NORMAL:
                logger.info("\n" + "="*80)
                logger.info("\u2705 WORKFLOW COMPLETED SUCCESSFULLY")
                logger.info("="*80)
            
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            results["status"] = "failed"
            results["errors"].append(str(e))
        
        # Save results to file
        results_file = job_dir / "workflow_results.json"
        try:
            with open(results_file, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"Results saved to: {results_file}")
        except IOError as e:
            logger.error("Failed to save results to %s: %s", results_file, e)
        
        return results
