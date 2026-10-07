"""
Workflow Orchestrator

Coordinates execution of molecular docking workflow across MCP services.
"""
import copy
import json
import logging
import math
import shutil
import threading
import time
import traceback
import uuid
import requests
from typing import Dict, Any, List, Optional
from pathlib import Path

from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM
from common.docking_stop import clear_docking_stop
from common.file_io import FileLock, atomic_write_json, load_json_dict
from common.file_utils import scan_ligand_folder
from common.http_retry import build_mcp_retry_session
from common.job_log import append_job_log
from common.mcp_client import call_mcp_tool as _shared_call_mcp_tool
from common.path_utils import to_container_path
from common.utils import ensure_shared_directory
from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS, get_service_urls_from_env

logger = logging.getLogger(__name__)

# Retries only connection-establishment failures, never read timeouts/5xx --
# see common/http_retry.py for why that distinction matters for a call that
# can trigger a real side-effecting action (e.g. run_docking).
_mcp_session = build_mcp_retry_session()

# Verbosity levels (match nlp_orchestrator.py)
VERBOSITY_LOW = 0       # Minimal output (step summaries only)
VERBOSITY_NORMAL = 1    # Normal output (current behavior)
VERBOSITY_MCP_DEMO = 2  # Full MCP demonstration mode (show JSON-RPC messages)


def docking_timeout_seconds(num_ligands: int, workers: int, algorithm: str, exhaustiveness: int) -> int:
    """HTTP timeout for one run_docking call.

    moldina-runner docks one ligand at a time (or up to *workers* at once),
    so the timeout scales with batch size / parallelism -- a flat 300 s cap
    silently fails any sequential batch of more than a couple of ligands.

    Per-ligand budget: 120 s for PSO (~2-3 s measured, it ignores
    exhaustiveness). Vina's Monte Carlo search grows with exhaustiveness, so
    it gets 30 s per exhaustiveness unit (240 s at the default 8; measured
    ~62-106 s per ligand alone and ~144 s for a 10-worker wave,
    docs/BATCH_DOCKING.md). No credit is given for cpu > 1: Vina is
    CPU-bound, so splitting the quota into fewer, multi-CPU workers doesn't
    shorten the batch -- and the server may clamp workers to quota // cpu,
    below the *workers* asked for (Phase 15 R1, docs/reviews/stage_15_r1.md).
    """
    if algorithm == "vina":
        per_ligand = max(120, 30 * max(1, int(exhaustiveness)))
    else:
        per_ligand = 120
    return max(300, math.ceil(per_ligand * num_ligands / max(1, workers)))


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
        ensure_shared_directory(self.output_base)
        
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

        Thin wrapper around common.mcp_client.call_mcp_tool -- this and
        that module used to duplicate the same JSON-RPC envelope-building
        and error-handling logic independently (and had already drifted:
        this method generated a per-job request_id, the shared one defaulted
        to a static "1"). This keeps the orchestrator-specific behavior
        (per-job request IDs, MCP-demo verbosity printing) as hooks passed
        into the one real implementation.
        """
        # Generate unique ID for each request (required by JSON-RPC 2.0)
        request_id = f"orchestrator_{self.current_job_id}_{str(uuid.uuid4())[:8]}"

        logger.info(f"Calling {tool_name} on {service_url}")
        # Only log arguments in normal verbosity (not in MCP demo mode where we already show full payload)
        if self.verbosity != VERBOSITY_MCP_DEMO:
            logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")

        def _print_request(payload: Dict[str, Any]) -> None:
            if self.verbosity == VERBOSITY_MCP_DEMO:
                print("\n" + "." * 80)
                print(f"📡 MCP Client → Server ({service_url})")
                print("." * 80)
                print()
                print("JSON-RPC:")
                print(json.dumps(payload, indent=2))
                print("─" * 80)

        def _print_response(data: Dict[str, Any]) -> None:
            if self.verbosity == VERBOSITY_MCP_DEMO:
                print("." * 80)
                print(f"📡 MCP Server ({service_url}) → Client")
                print("." * 80)
                print()
                print("JSON-RPC:")
                print(json.dumps(data, indent=2))
                print("─" * 80 + "\n")

        # No current_job_id (e.g. a call made outside execute_workflow()) has
        # nowhere well-defined to log to -- skip rather than fall back to a
        # shared "unknown_job" directory that would interleave lines from
        # unrelated calls.
        job_dir = self.output_base / self.current_job_id if self.current_job_id else None
        call_start = time.time()
        try:
            result = _shared_call_mcp_tool(
                service_url, tool_name, arguments,
                request_id=request_id,
                timeout=timeout,
                wrap_connection_errors=True,
                on_request=_print_request,
                on_response=_print_response,
            )
        except RuntimeError as e:
            logger.error(str(e))
            if job_dir is not None:
                append_job_log(
                    job_dir, "ERROR",
                    f"MCP call failed: {tool_name} @ {service_url} "
                    f"(after {time.time() - call_start:.1f}s): {e}",
                )
            raise

        if job_dir is not None:
            append_job_log(
                job_dir, "INFO",
                f"MCP call: {tool_name} @ {service_url} completed in {time.time() - call_start:.1f}s",
            )
        return result if result is not None else {}
    
    def _copy_local_input(self, source_path: str, dest_dir: Path) -> str:
        """Copy a user-supplied local file into the job's downloads dir and return its container path."""
        src = Path(source_path)
        if not src.is_file():
            raise RuntimeError(f"Local input file not found: {source_path}")
        dest = dest_dir / src.name
        shutil.copy(src, dest)
        return self.normalize_path(str(dest))

    def _copy_local_inputs(self, source_paths: List[str], dest_dir: Path) -> List[str]:
        """Copy multiple user-supplied local files into the job's downloads dir.

        Raises RuntimeError if two *different* source files would collide on
        the same destination basename -- silently letting one overwrite the
        other would corrupt the docking inputs with no indication anything
        went wrong. The same source path appearing twice (e.g. the user
        intentionally lists one file twice) is not a collision -- it copies
        to the same destination idempotently.
        """
        claimed: Dict[str, str] = {}  # basename -> resolved source path that claimed it
        for source_path in source_paths:
            name = Path(source_path).name
            resolved = str(Path(source_path).resolve())
            if name in claimed and claimed[name] != resolved:
                raise RuntimeError(
                    f"Local ligand filename collision: both {claimed[name]} and "
                    f"{source_path} would be copied to the same destination "
                    f"filename '{name}'. Rename one of the files and try again."
                )
            claimed[name] = resolved
        return [self._copy_local_input(f, dest_dir) for f in source_paths]

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
        job_name: str = "",
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """
        Execute a complete workflow.

        Args:
            workflow: Validated workflow dictionary
            job_id: Optional job identifier
            state_manager: Optional persistence manager used for checkpointing
            job_name: Optional user-supplied display label for the job
            cancel_event: Optional cooperative-cancellation flag (e.g. from a
                GUI "Stop" button). Checked once before each of the 5 steps --
                not mid-request, so a step's own in-flight MCP call (capped at
                its own timeout, see common/mcp_client.py) still runs to
                completion; cancellation only prevents the *next* step from
                starting. The one exception is the dock step: a stop writes a
                flag file that moldina-runner polls mid-run
                (common/docking_stop.py), and a ``"stopped"`` dock result
                cancels the workflow. `None` (the default, used by every CLI
                caller) means cancellation is never checked -- zero behavior
                change.

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
        ensure_shared_directory(job_dir)

        results = {
            "job_id": self.current_job_id,
            "job_name": job_name or "",
            "services_executed": [],
            "results": {},
            "errors": []
        }

        logger.info(f"Starting workflow execution: {self.current_job_id}")
        logger.info(f"Services to execute: {services}")

        workflow_start = time.time()

        def _log_step_done(step: str, start_time: float, detail: str) -> None:
            append_job_log(job_dir, "INFO", f"{step} completed in {time.time() - start_time:.1f}s: {detail}")

        append_job_log(
            job_dir, "INFO",
            f"Starting workflow execution: job_id={self.current_job_id} "
            f"job_name={job_name or '(none)'} services={services}",
        )

        cancelled = False

        # NOTE (R2, Stage 11.2/11.3 review, docs/reviews/stage_11_2_11_3_r2.md
        # finding #12): the "<step> in services and not _maybe_cancel()" guard
        # below is pasted identically onto all 5 step conditions rather than
        # checked once inside a shared loop/dispatch point -- the same
        # duplication class R1 already collapsed once for the per-step
        # append_job_log() calls (see _log_step_done above). Deferred here
        # because fixing it properly means restructuring this whole method
        # into a step-list-driven loop, a materially bigger change than a
        # review pass should make. Revisit next time a 6th workflow step is
        # added (the highest-risk moment: a new step guard copy-pasted
        # without the "and not _maybe_cancel()" clause would silently make
        # Stop not apply to it) or when this method is next restructured for
        # an unrelated reason.
        def _maybe_cancel() -> bool:
            # Checked once at the top of each step below, right after
            # confirming that step was even requested -- a step already in
            # flight (e.g. a single MCP call) is never interrupted mid-call,
            # only the *next* one is prevented from starting.
            nonlocal cancelled
            if not cancelled and cancel_event is not None and cancel_event.is_set():
                cancelled = True
                logger.info(
                    "Workflow %s: cancellation requested, stopping before next step",
                    self.current_job_id,
                )
            return cancelled

        try:
            # Step 1: Get input files (RCSB download and/or local file copy)
            if "get_input_files" in services and not _maybe_cancel():
                _reload_latest_from_disk()
                step_t0 = time.time()

                self._step_print("\n\U0001f4e5 STEP 1: Getting input files...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 1: Getting input files")
                    logger.info("="*80)
                
                download_dir = job_dir / "downloads"
                ensure_shared_directory(download_dir)

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

                local_ligand_paths = self._copy_local_inputs(local_ligand_files, download_dir)
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
                _log_step_done(
                    "get_input_files", step_t0,
                    f"got protein ({protein_source}) and {len(self.workflow_state['ligand_files'])} "
                    f"ligand(s) ({ligand_source})",
                )

                _save_step_checkpoint("get_input_files")
            
            # Step 2: Prepare ligands
            if "prepare_ligands" in services or "prepare_protein" in services:
                prepared_dir = job_dir / "prepared"
                ensure_shared_directory(prepared_dir)
            
            if "prepare_ligands" in services and not _maybe_cancel():
                _reload_latest_from_disk()
                step_t0 = time.time()

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
                _log_step_done("prepare_ligands", step_t0, f"prepared {len(prepared_ligands)} ligand(s)")

                _save_step_checkpoint("prepare_ligands")
            
            # Step 3: Prepare protein
            if "prepare_protein" in services and not _maybe_cancel():
                _reload_latest_from_disk()
                step_t0 = time.time()

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
                    self.workflow_state["prepared_protein_pdb"] = protein_results[0].get("pdb_path")
                    self._step_print("   \u2705 Protein prepared successfully")
                    if self.verbosity == VERBOSITY_NORMAL:
                        logger.info(f"\u2705 Prepared protein: {self.workflow_state['prepared_protein']}")
                    _log_step_done("prepare_protein", step_t0, f"{self.workflow_state['prepared_protein']}")

                _save_step_checkpoint("prepare_protein")
            
            # Step 4: Run docking
            docking_dir = job_dir / "docking_results"
            if "dock" in services and not cancelled:
                # Stop protocol (common/docking_stop.py): create the shared
                # dir and drop a stale flag left by an earlier stopped run
                # *before* the step's own cancel check below. A stop arriving
                # after this point is either seen by that check or writes a
                # fresh flag that moldina-runner picks up mid-run.
                ensure_shared_directory(docking_dir)
                clear_docking_stop(docking_dir)
            if "dock" in services and not _maybe_cancel():
                _reload_latest_from_disk()
                step_t0 = time.time()

                self._step_print("\n\u26db\ufe0f  STEP 4: Running molecular docking...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 4: Running molecular docking")
                    logger.info("="*80)
                
                if not self.workflow_state.get("prepared_protein"):
                    raise RuntimeError("No prepared protein available for docking")
                if not self.workflow_state.get("prepared_ligands"):
                    raise RuntimeError("No prepared ligands available for docking")
                
                if self.verbosity != VERBOSITY_LOW:
                    logger.info(f"Docking parameters:")
                    logger.info(f"  Algorithm: {params.get('docking_algorithm', DEFAULT_DOCKING_ALGORITHM)}")
                    logger.info(f"  Center: {params['center']}")
                    logger.info(f"  Box Size: {params['box_size']}")
                    logger.info(f"  Exhaustiveness: {params.get('exhaustiveness', 8)}")
                    logger.info(f"  Population: {params.get('population', 500)}")
                
                parallel = bool(params.get("parallel", False))
                max_workers = max(1, int(params.get("max_workers", 1)))
                algorithm = params.get("docking_algorithm", DEFAULT_DOCKING_ALGORITHM)
                dock_timeout = docking_timeout_seconds(
                    len(self.workflow_state["prepared_ligands"]),
                    max_workers if parallel else 1,
                    algorithm,
                    params.get("exhaustiveness", 8),
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
                        "docking_algorithm": algorithm,
                    },
                    timeout=dock_timeout,
                )
                
                results["results"]["dock"] = docking_result

                if docking_result.get("stopped"):
                    # Stopped mid-docking via the flag file: keep the partial
                    # result on record, but don't count dock as executed or
                    # checkpoint it -- a later redock must dock everything
                    # again. Marking the run cancelled also skips visualize.
                    cancelled = True
                    self._step_print(
                        f"   ⏹ Docking stopped: {docking_result.get('successful', 0)} docked, "
                        f"{docking_result.get('skipped', 0)} not docked"
                    )
                    _log_step_done(
                        "dock", step_t0,
                        f"stopped on request -- {docking_result.get('successful', 0)} docked, "
                        f"{docking_result.get('skipped', 0)} not docked",
                    )
                else:
                    results["services_executed"].append("dock")

                    # Store docking results
                    self.workflow_state["docking_results"] = docking_result
                    self._step_print(f"   \u2705 Docking completed: {docking_result.get('successful', 0)} successful")
                    if self.verbosity == VERBOSITY_NORMAL:
                        logger.info(f"\u2705 Docking completed: {docking_result.get('successful', 0)} successful")
                    _log_step_done(
                        "dock", step_t0,
                        f"{docking_result.get('successful', 0)} successful. "
                        f"Per-ligand docking logs: {docking_dir}/<ligand_id>_moldina_output.log",
                    )

                    _save_step_checkpoint("dock")
            
            # Step 5: Visualize
            if "visualize" in services and not _maybe_cancel():
                _reload_latest_from_disk()
                step_t0 = time.time()

                self._step_print("\n\U0001f3a8 STEP 5: Generating visualization...")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("STEP 5: Generating visualization")
                    logger.info("="*80)
                
                if not self.workflow_state.get("prepared_protein_pdb"):
                    raise RuntimeError("No prepared protein available for visualization")

                # Ligand poses (with per-mode affinity/RMSD) are converted from the
                # docked PDBQT to SDF for client-side (3Dmol.js) viewing; the receptor
                # needs no conversion, the already-prepared PDB is used as-is.
                pose_results = []
                if self.workflow_state.get("docking_results"):
                    pose_results = [
                        r for r in self.workflow_state["docking_results"].get("results", [])
                        if r.get("output_path")
                    ]

                viz_dir = job_dir / "visualization"
                ensure_shared_directory(viz_dir)

                if pose_results:
                    # ligand-preparer converts poses one ligand at a time (Meeko
                    # round-trip, up to 3 formats each), so like dock_timeout above,
                    # the flat 300s default silently fails on large batches.
                    viz_timeout = max(300, math.ceil(5 * len(pose_results)))

                    viz_result = self.call_mcp_tool(
                        self.services["ligand_preparer"],
                        "convert_docked_poses",
                        {
                            "pose_results": pose_results,  # Already in container format
                            "output_dir": self.normalize_path(viz_dir),
                        },
                        timeout=viz_timeout,
                    )
                else:
                    if self.verbosity != VERBOSITY_LOW:
                        logger.warning("No docked ligand poses found, skipping pose conversion")
                    viz_result = {"converted": [], "errors": [], "status": "skipped"}

                viz_result["receptor_pdb"] = self.workflow_state["prepared_protein_pdb"]

                results["results"]["visualize"] = viz_result
                results["services_executed"].append("visualize")
                
                self._step_print(f"   \u2705 Visualization prepared: {len(viz_result.get('converted', []))} ligand(s)")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info(f"\u2705 Visualization prepared: {viz_result.get('converted')}")
                _log_step_done("visualize", step_t0, f"{len(viz_result.get('converted', []))} ligand(s)")

                _save_step_checkpoint("visualize")

            if cancelled:
                results["status"] = "cancelled"
                results["errors"].append("Workflow cancelled by user request")
                self._step_print("\n\u23f9 WORKFLOW CANCELLED (stopped before the next step)\n")
                if self.verbosity == VERBOSITY_NORMAL:
                    logger.info("\n" + "="*80)
                    logger.info("\u23f9 WORKFLOW CANCELLED")
                    logger.info("="*80)
            else:
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
            append_job_log(job_dir, "ERROR", traceback.format_exc())

        append_job_log(
            job_dir, "INFO",
            f"Workflow finished: job_id={self.current_job_id} status={results['status']} "
            f"elapsed={time.time() - workflow_start:.1f}s",
        )

        # Merge with any existing on-disk results so a session spanning more
        # than one execute_workflow() call (e.g. a redock after a parameter
        # change) doesn't lose earlier steps' results from the persisted
        # snapshot -- this used to clobber on every call. No-op for a fresh
        # job_dir (nothing to merge with). Locked + atomic: two overlapping
        # execute_workflow() calls against the same job_dir (e.g. GUI and
        # CLI both redocking) must not race a read-modify-write here, the
        # same hazard _next_revision() guards against for the manifest.
        #
        # NOTE (R1, Stage 11.1 review, docs/reviews/stage_11_1_r1.md finding
        # #2): the merge only carries forward per-step results wholesale --
        # it does not check whether a downstream step's persisted result is
        # still valid for the upstream inputs this call just produced (e.g.
        # a call that reruns get_input_files+dock but not visualize would
        # leave a stale visualize entry from an earlier protein/ligand set
        # merged in as if current). Deferred: today every caller reaches
        # execute_workflow() via rerun_planner.compute_rerun_steps(), which
        # always includes every downstream step when an upstream one
        # reruns, so the stale-merge case cannot occur in practice yet. (A
        # run that stops or fails part-way did hit it; that case is handled
        # below -- Review Stage R1, Phase 13.)
        # Revisit if a caller ever invokes execute_workflow() with a
        # services list computed by something other than
        # compute_rerun_steps() (see dev_plan.md Future Work).
        results_lock_file = job_dir / ".workflow_results.lock"
        results_file = job_dir / "workflow_results.json"
        try:
            with FileLock(results_lock_file):
                existing_results = load_json_dict(results_file)
                if existing_results:
                    merged_step_results = dict(existing_results.get("results", {}))
                    merged_step_results.update(results.get("results", {}))
                    # A step this call was asked to run but didn't finish
                    # (stopped, cancelled or failed part-way) is no longer
                    # executed, and an older result for it is stale: drop it
                    # unless this call recorded one of its own (a stopped
                    # dock's partial result). Without this a stopped redock
                    # counted dock as executed and kept the previous run's
                    # visualize next to it (Review Stage R1, Phase 13).
                    this_run = set(results["services_executed"])
                    for step in set(services) - this_run:
                        if step not in results.get("results", {}):
                            merged_step_results.pop(step, None)
                    previously_executed = set(existing_results.get("services_executed", [])) - set(services)
                    results["results"] = merged_step_results
                    results["services_executed"] = [
                        s for s in WORKFLOW_STEPS if s in this_run or s in previously_executed
                    ]
                    if not results.get("job_name") and existing_results.get("job_name"):
                        results["job_name"] = existing_results["job_name"]

                atomic_write_json(results_file, results)
            logger.info(f"Results saved to: {results_file}")
        except OSError as e:
            logger.error("Failed to save results to %s: %s", results_file, e)

        return results
