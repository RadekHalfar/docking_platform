"""
LLM Orchestrator CLI

Command-line interface for natural language workflow orchestration.
"""
import sys
import os
import logging
import argparse
import subprocess
import time
import requests
from typing import Optional, List, Callable, Dict, Any

from services.llm_orchestrator import (
    BaseLLMClient,
    WorkflowOrchestrator,
    WorkflowParser,
    WorkflowStateManager,
    InteractiveSession,
    create_llm_client,
    parse_workflow_with_llm,
    generate_llm_follow_up_question,
    extract_updates_with_llm,
    extract_box_source_choice_with_llm,
)

# Verbosity levels
VERBOSITY_LOW = 0       # Minimal output (step summaries only)
VERBOSITY_NORMAL = 1    # Normal output (current behavior)
VERBOSITY_MCP_DEMO = 2  # Full MCP demonstration mode (show JSON-RPC messages)

# Global verbosity setting
current_verbosity = VERBOSITY_NORMAL

# Setup logging with custom filter
class VerbosityFilter(logging.Filter):
    """Filter log messages based on verbosity level."""
    
    def filter(self, record):
        global current_verbosity
        
        # In low verbosity, suppress all service logs
        if current_verbosity == VERBOSITY_LOW:
            # Only allow messages from nlp_orchestrator main module
            if not record.name.startswith('__main__'):
                return False
        
        # In MCP demo mode, suppress all logs (we show custom formatted output)
        elif current_verbosity == VERBOSITY_MCP_DEMO:
            # Suppress all logging - we'll use print statements for clean MCP protocol display
            return False
        
        # In normal verbosity, show all INFO logs (current behavior)
        elif current_verbosity == VERBOSITY_NORMAL:
            return True
        
        return True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CLARIFICATION_MODE_HYBRID = "hybrid"
CLARIFICATION_MODE_LEGACY = "legacy"

# Add verbosity filter to root logger
verbosity_filter = VerbosityFilter()
for handler in logging.getLogger().handlers:
    handler.addFilter(verbosity_filter)


def _print_interactive_help() -> None:
    """Print supported interactive session commands."""
    print("\nSupported commands:")
    print("  - Natural language workflow request (first run in session)")
    print("  - new <workflow request>  # Start a fresh workflow in current session")
    print("  - state                   # Show active job state from disk")
    print("  - help                    # Show this help")
    print("  - end / quit / exit       # End session")


def _run_interactive_loop(
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    clarification_mode: str,
) -> int:
    """Run phase-1 interactive session loop with persistent active job context."""
    session = InteractiveSession()

    print("=" * 80)
    print("LLM Workflow Orchestrator - Interactive Session")
    print("=" * 80)
    print("\nType your workflow request in natural language.")
    print("Example: 'Download ligands ATP, HEM and protein 6lu7, prepare and dock'")
    print("\nAfter first workflow completes, use 'state', 'new <request>', or 'end'.")
    print("Type 'help' to see all commands.\n")

    while session.session_active:
        try:
            user_input = input("🧬 > ").strip()

            if not user_input:
                continue

            normalized = user_input.lower()

            if normalized in ["quit", "exit", "q", "end"]:
                session.session_active = False
                print("Goodbye!")
                break

            if normalized in ["help", "h", "?"]:
                _print_interactive_help()
                continue

            if normalized == "state":
                print("\n" + session.format_state_summary())
                continue

            if normalized.startswith("new "):
                new_request = user_input[4:].strip()
                if not new_request:
                    print("Please provide a workflow request after 'new'.")
                    continue

                process_workflow(
                    new_request,
                    llm_client,
                    orchestrator,
                    parser_obj,
                    clarification_mode=clarification_mode,
                    session=session,
                )
                continue

            if session.has_active_job():
                print(
                    "Active job is already loaded. Use 'state', 'new <request>', 'help', or 'end'."
                )
                continue

            process_workflow(
                user_input,
                llm_client,
                orchestrator,
                parser_obj,
                clarification_mode=clarification_mode,
                session=session,
            )

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            print(f"❌ Error: {e}")

    return 0


def main():
    """Main entry point for orchestrator CLI."""
    parser = argparse.ArgumentParser(
        description="Natural Language Molecular Docking Workflow Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default (low verbosity) - minimal output
  python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them and dock with center 10,20,30 and box size 25,30,35"
  
  # Normal verbosity - show service logs
  python nlp_orchestrator.py --verbose normal "Download 6lu7 and ATP, prepare and dock"
  
  # MCP demo mode - show full JSON-RPC communication
  python nlp_orchestrator.py --verbose mcp_demo "Download 6lu7 and ATP, prepare and dock"
  
  # Interactive mode
  python nlp_orchestrator.py --interactive
  
  # Health check
  python nlp_orchestrator.py --check-health
"""
    )
    
    parser.add_argument(
        "command",
        nargs="?",
        help="Natural language workflow command"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Run in interactive mode"
    )
    parser.add_argument(
        "--llm-provider",
        default=os.getenv("LLM_PROVIDER", "ollama"),
        help="LLM provider name (default: ollama)"
    )
    parser.add_argument(
        "--llm-endpoint",
        default=os.getenv("LLM_ENDPOINT", "http://localhost:11434"),
        help="LLM API endpoint URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"),
        help="LLM model name to use (default: qwen2.5-coder:7b-instruct-q4_K_M)"
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.getenv("LLM_API_KEY"),
        help="Optional API key for LLM providers that require authentication"
    )
    parser.add_argument(
        "--output-dir",
        default="./orchestrator_output",
        help="Base directory for workflow outputs (default: ./orchestrator_output)"
    )
    parser.add_argument(
        "--check-health",
        action="store_true",
        help="Check health of all services and exit"
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models from selected LLM provider and exit"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--verbose",
        choices=["low", "normal", "mcp_demo"],
        default="low",
        help="Verbosity level: 'low' (minimal, step summaries only), 'normal' (show service logs), 'mcp_demo' (full MCP JSON-RPC demonstration)"
    )
    parser.add_argument(
        "--clarification-mode",
        choices=[CLARIFICATION_MODE_HYBRID, CLARIFICATION_MODE_LEGACY],
        default=os.getenv("CLARIFICATION_MODE", CLARIFICATION_MODE_HYBRID),
        help="Missing-parameter clarification mode: 'hybrid' (default, LLM + deterministic checks) or 'legacy' (deterministic-only)."
    )
    
    args = parser.parse_args()
    
    # Set verbosity level
    global current_verbosity
    if args.verbose == "low":
        current_verbosity = VERBOSITY_LOW
    elif args.verbose == "normal":
        current_verbosity = VERBOSITY_NORMAL
    elif args.verbose == "mcp_demo":
        current_verbosity = VERBOSITY_MCP_DEMO
        # Don't set DEBUG level - we'll use custom output instead
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize components
    llm_client = create_llm_client(
        provider=args.llm_provider,
        endpoint=args.llm_endpoint,
        model=args.llm_model,
        api_key=args.llm_api_key,
    )
    orchestrator = WorkflowOrchestrator(output_base=args.output_dir, verbosity=current_verbosity)
    parser_obj = WorkflowParser()
    
    # Handle special commands
    if args.list_models:
        print(f"Available models for provider '{args.llm_provider}':")
        models = llm_client.list_models()
        if models:
            for model in models:
                print(f"  - {model}")
        else:
            print("  No models found or provider endpoint not accessible")
        return 0
    
    if args.check_health:
        print("Checking service health...")
        print("\nLLM Provider:")
        ollama_ok = llm_client.check_health()
        print(f"  {'✅' if ollama_ok else '❌'} {args.llm_provider} ({args.llm_endpoint})")
        
        print("\nMCP Services:")
        health = orchestrator.check_services_health()
        for service, ok in health.items():
            print(f"  {'✅' if ok else '❌'} {service}")
        
        all_ok = ollama_ok and all(health.values())
        if all_ok:
            print("\n✅ All services are healthy!")
            return 0
        else:
            print("\n❌ Some services are not healthy. Please start them first.")
            return 1
    
    # Interactive mode
    if args.interactive:
        return _run_interactive_loop(
            llm_client=llm_client,
            orchestrator=orchestrator,
            parser_obj=parser_obj,
            clarification_mode=args.clarification_mode,
        )
    
    # Single command mode
    if args.command:
        return process_workflow(
            args.command,
            llm_client,
            orchestrator,
            parser_obj,
            clarification_mode=args.clarification_mode,
        )
    else:
        parser.print_help()
        return 1


def _ask_yes_no(question: str, default_yes: bool = True) -> bool:
    """Ask a yes/no question and return True for yes, False for no."""
    while True:
        response = input(question).strip().lower()
        if not response:
            return default_yes
        if response in ["y", "yes"]:
            return True
        if response in ["n", "no"]:
            return False
        print("Please answer with 'y' or 'n'.")


def _parse_coordinate_triplet(value: str) -> Optional[List[float]]:
    """Parse coordinates from formats like '10,20,30' or '[10, 20, 30]'"""
    normalized = value.strip().strip("[]()").replace(" ", "")
    if not normalized:
        return None

    parts = normalized.split(",")
    if len(parts) != 3:
        return None

    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def _generate_job_id() -> str:
    """Generate a job id to keep all workflow artifacts under one folder."""
    return f"job_{int(time.time() * 1000)}"


def _collect_missing_parameters_hybrid(
    parser_obj: WorkflowParser,
    output_base: str,
    llm_client: BaseLLMClient,
    max_turns: int = 8,
) -> None:
    """Hybrid clarification: deterministic branching + LLM wording/intent extraction."""
    prompt_order = ["protein_id", "ligand_ids", "center", "box_size"]
    center_default = [10.0, 20.0, 30.0]
    box_size_default = [25.0, 30.0, 35.0]
    box_source_decided = False
    manual_box_mode = False
    turn = 0

    while parser_obj.has_missing_parameters() and turn < max_turns:
        turn += 1
        missing = parser_obj.get_missing_parameters()
        current_param = next((p for p in prompt_order if p in missing), missing[0])

        if current_param in ["center", "box_size"] and not box_source_decided:
            source_answer = input(
                "How should we define docking box coordinates? "
                "(manually define coordinates or interactive PyMOL): "
            ).strip()
            box_source_choice = extract_box_source_choice_with_llm(llm_client, source_answer)

            if box_source_choice == "pymol":
                box_source_decided = True
                used_pymol = _try_apply_live_pymol_box(
                    parser_obj,
                    live_box_provider=getattr(parser_obj, "_live_box_provider", None),
                )
                if used_pymol:
                    parser_obj.validate_workflow(
                        {
                            "services": parser_obj.parsed_workflow["services"],
                            "parameters": parser_obj.parsed_workflow["parameters"],
                        }
                    )
                    continue
                if not _ask_yes_no(
                    "Could not define docking box in PyMOL now. Continue with manual coordinates/defaults? [Y/n]: ",
                    default_yes=True,
                ):
                    box_source_decided = False
                    continue
                manual_box_mode = True

            elif box_source_choice == "manual":
                box_source_decided = True
                manual_box_mode = True

            else:
                print("I could not determine box mode from that answer. Please choose directly.")
                box_source_decided = True
                used_pymol = _choose_docking_box_source(
                    parser_obj,
                    output_base,
                    live_box_provider=getattr(parser_obj, "_live_box_provider", None),
                )
                if used_pymol:
                    parser_obj.validate_workflow(
                        {
                            "services": parser_obj.parsed_workflow["services"],
                            "parameters": parser_obj.parsed_workflow["parameters"],
                        }
                    )
                    continue
                manual_box_mode = True

        if current_param == "center" and not manual_box_mode:
            use_default = _ask_yes_no(
                "I am missing docking box center [x, y, z]. Do you want to use default [10, 20, 30]? [Y/n]: "
            )
            if use_default:
                parser_obj.update_parameters({"center": center_default})
                parser_obj.validate_workflow(
                    {
                        "services": parser_obj.parsed_workflow["services"],
                        "parameters": parser_obj.parsed_workflow["parameters"],
                    }
                )
                continue

        if current_param == "box_size" and not manual_box_mode:
            use_default = _ask_yes_no(
                "I am missing docking box size [x, y, z]. Do you want to use default [25, 30, 35]? [Y/n]: "
            )
            if use_default:
                parser_obj.update_parameters({"box_size": box_size_default})
                parser_obj.validate_workflow(
                    {
                        "services": parser_obj.parsed_workflow["services"],
                        "parameters": parser_obj.parsed_workflow["parameters"],
                    }
                )
                continue

        question = generate_llm_follow_up_question(llm_client, parser_obj, current_param)
        user_answer = input(f"{question.rstrip(':')} ").strip()

        updates = extract_updates_with_llm(
            llm_client=llm_client,
            parser_obj=parser_obj,
            user_answer=user_answer,
            target_param=current_param,
        )

        if updates:
            parser_obj.update_parameters(updates)
        else:
            print("I could not extract a valid value from that answer. Please try again.")

        parser_obj.validate_workflow(
            {
                "services": parser_obj.parsed_workflow["services"],
                "parameters": parser_obj.parsed_workflow["parameters"],
            }
        )


def _normalize_clarification_mode(mode: str) -> str:
    """Normalize clarification mode with hybrid as safe default."""
    if mode == CLARIFICATION_MODE_LEGACY:
        return CLARIFICATION_MODE_LEGACY
    return CLARIFICATION_MODE_HYBRID


def _create_box_via_live_pymol(
    orchestrator: WorkflowOrchestrator,
    workflow: Dict[str, Any],
    job_id: str,
    state_manager: Optional[WorkflowStateManager] = None,
) -> Optional[dict]:
    """Run pre-docking prep, open interactive PyMOL flow, and derive center/box_size from selection."""
    base_url = os.getenv("MCP_PYMOL_INTERACTIVE_URL", "http://localhost:8090").rstrip("/")
    no_vnc_url = os.getenv("PYMOL_INTERACTIVE_NOVNC_URL", "http://localhost:6080/vnc.html")

    def _service_healthy() -> bool:
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    # Ensure PyMOL Interactive is available.
    healthy = _service_healthy()
    if not healthy and not _is_running_in_container():
        if _try_start_pymol_interactive_service():
            for _ in range(30):
                if _service_healthy():
                    healthy = True
                    break
                time.sleep(1)

    if not healthy:
        print("pymol_interactive is not reachable.")
        print("Start it with: docker compose --profile interactive up -d pymol-interactive")
        print(f"Then open: {no_vnc_url}")
        return None

    # Run the preparation phase so interactive service can load prepared receptor/ligands.
    print("\n🧪 Preparing structures for interactive box selection...")
    prep_workflow = {
        "services": ["download", "prepare_ligands", "prepare_protein"],
        "parameters": workflow.get("parameters", {}),
    }
    prep_results = orchestrator.execute_workflow(prep_workflow, job_id=job_id, state_manager=state_manager)
    if prep_results.get("errors"):
        print("Could not prepare structures for PyMOL-guided box selection.")
        for err in prep_results["errors"]:
            print(f"  - {err}")
        return None

    prep_job_id = prep_results.get("job_id", job_id)

    # Load prepared assets into live PyMOL session.
    loaded = False
    for _ in range(30):
        try:
            response = requests.post(
                f"{base_url}/api/load_job",
                json={"job_id": prep_job_id},
                timeout=15,
            )
            if response.status_code == 200:
                loaded = True
                break
        except requests.RequestException:
            pass
        time.sleep(1)

    if not loaded:
        print("Could not auto-load prepared structures into interactive PyMOL.")
        print(f"Open noVNC manually: {no_vnc_url}")
        return None

    print("\n✅ Interactive PyMOL is ready for box selection.")
    print(f"Open noVNC: {no_vnc_url}")
    print("In PyMOL, create/select your target region, then type selection name (default: sele).")

    selection = input("Selection name to use for docking box [sele]: ").strip() or "sele"

    try:
        response = requests.post(
            f"{base_url}/api/box/from_selection",
            json={"job_id": prep_job_id, "selection": selection},
            timeout=30,
        )
        if response.status_code != 200:
            print(f"Failed to derive docking box from selection '{selection}'.")
            print(f"API response: {response.text}")
            return None

        payload = response.json()
        return {
            "center": payload.get("center"),
            "box_size": payload.get("box_size"),
            "job_id": prep_job_id,
            "path": payload.get("saved_to", ""),
        }
    except requests.RequestException as exc:
        print(f"Could not contact pymol_interactive API: {exc}")
        return None


def _choose_docking_box_source(
    parser_obj: WorkflowParser,
    output_base: str,
    live_box_provider: Optional[Callable[[], Optional[dict]]] = None,
) -> bool:
    """Ask user whether to type box values manually or define a new box in PyMOL.

    Returns True when coordinates were populated from PyMOL box, False for manual flow.
    """
    while True:
        print("\nDocking box coordinates are missing.")
        print("Choose how to provide them:")
        print("  1) Write center/box_size manually")
        print("  2) Define a new box in PyMOL Interactive now")
        choice = input("Select [1/2] (default: 1): ").strip().lower()

        if choice in ["", "1", "manual", "write", "w"]:
            return False

        if choice in ["2", "pymol", "visual", "v"]:
            if _try_apply_live_pymol_box(parser_obj, live_box_provider):
                return True
            if _ask_yes_no("Do you want to enter coordinates manually now? [Y/n]: ", default_yes=True):
                return False
            continue

        print("Please choose 1 or 2.")


def _try_apply_live_pymol_box(
    parser_obj: WorkflowParser,
    live_box_provider: Optional[Callable[[], Optional[dict]]],
) -> bool:
    """Try to populate center/box_size from interactive PyMOL selection."""
    if not live_box_provider:
        print("PyMOL-guided box definition is not available in this run.")
        return False

    box_payload = live_box_provider()
    if not box_payload:
        print("Could not define docking box from interactive PyMOL.")
        return False

    center = box_payload.get("center")
    box_size = box_payload.get("box_size")
    if not parser_obj.validate_coordinates(center) or not parser_obj.validate_coordinates(box_size):
        print("PyMOL returned invalid coordinates.")
        return False

    parser_obj.update_parameters({"center": center, "box_size": box_size})
    print("✅ Loaded docking box from live PyMOL session:")
    print(f"   center: {center}")
    print(f"   box_size: {box_size}")
    print(f"   source: {box_payload.get('path', 'pymol_interactive')}")
    return True


def _collect_missing_parameters(parser_obj: WorkflowParser, output_base: str) -> None:
    """Collect missing or invalid required fields in short step-by-step prompts."""
    prompt_order = ["protein_id", "ligand_ids", "center", "box_size"]
    center_default = [10.0, 20.0, 30.0]
    box_size_default = [25.0, 30.0, 35.0]
    box_source_decided = False

    while parser_obj.has_missing_parameters():
        missing = parser_obj.get_missing_parameters()
        current_param = next((p for p in prompt_order if p in missing), missing[0])

        if current_param in ["center", "box_size"] and not box_source_decided:
            box_source_decided = True
            used_pymol = _choose_docking_box_source(
                parser_obj,
                output_base,
                live_box_provider=getattr(parser_obj, "_live_box_provider", None),
            )
            if used_pymol:
                # Re-run validation and continue with any other missing fields.
                parser_obj.validate_workflow(
                    {
                        "services": parser_obj.parsed_workflow["services"],
                        "parameters": parser_obj.parsed_workflow["parameters"],
                    }
                )
                continue

        if current_param == "protein_id":
            issue = parser_obj.get_parameter_issue("protein_id")
            while True:
                if issue == "invalid":
                    prompt = "The protein PDB ID looks invalid. Please enter a valid 4-character ID (e.g., 6lu7): "
                else:
                    prompt = "I am missing the protein PDB ID. Please enter it (e.g., 6lu7): "

                value = input(prompt).strip()
                if parser_obj.validate_protein_id(value):
                    parser_obj.update_parameters({"protein_id": value})
                    break
                print("That protein ID looks invalid. It should be 4 alphanumeric characters.")

        elif current_param == "ligand_ids":
            while True:
                value = input("I am missing ligand IDs. Please provide comma-separated IDs (e.g., ATP,HEM): ").strip()
                ligands = [x.strip() for x in value.split(",") if x.strip()]
                if ligands:
                    parser_obj.update_parameters({"ligand_ids": ligands})
                    break
                print("I need at least one ligand ID.")

        elif current_param == "center":
            use_default = _ask_yes_no(
                "I am missing docking box center [x, y, z]. Do you want to use default [10, 20, 30]? [Y/n]: "
            )
            if use_default:
                parser_obj.update_parameters({"center": center_default})
            else:
                while True:
                    value = input("Please enter docking box center as x,y,z (e.g., 10,20,30): ").strip()
                    coords = _parse_coordinate_triplet(value)
                    if coords and parser_obj.validate_coordinates(coords):
                        parser_obj.update_parameters({"center": coords})
                        break
                    print("Center format is invalid. Please use exactly 3 numbers, for example: 10,20,30")

        elif current_param == "box_size":
            use_default = _ask_yes_no(
                "I am missing docking box size [x, y, z]. Do you want to use default [25, 30, 35]? [Y/n]: "
            )
            if use_default:
                parser_obj.update_parameters({"box_size": box_size_default})
            else:
                while True:
                    value = input("Please enter docking box size as x,y,z (e.g., 25,30,35): ").strip()
                    sizes = _parse_coordinate_triplet(value)
                    if sizes and parser_obj.validate_coordinates(sizes):
                        parser_obj.update_parameters({"box_size": sizes})
                        break
                    print("Box size format is invalid. Please use exactly 3 numbers, for example: 25,30,35")

        # Re-run validation so missing/invalid fields are always refreshed.
        parser_obj.validate_workflow(
            {
                "services": parser_obj.parsed_workflow["services"],
                "parameters": parser_obj.parsed_workflow["parameters"],
            }
        )


def _is_running_in_container() -> bool:
    """Best-effort check whether this process runs inside a container."""
    return os.path.exists("/.dockerenv")


def _try_start_pymol_interactive_service() -> bool:
    """Try to start pymol-interactive via docker compose on host runtime."""
    try:
        result = subprocess.run(
            ["docker", "compose", "--profile", "interactive", "up", "-d", "pymol-interactive"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _integrate_with_pymol_interactive(results: dict) -> None:
    """When visualization is requested, load job in interactive PyMOL if available."""
    if "visualize" not in results.get("services_executed", []):
        return

    base_url = os.getenv("MCP_PYMOL_INTERACTIVE_URL", "http://localhost:8090").rstrip("/")
    no_vnc_url = os.getenv("PYMOL_INTERACTIVE_NOVNC_URL", "http://localhost:6080/vnc.html")
    job_id = results.get("job_id")

    def _service_healthy() -> bool:
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    healthy = _service_healthy()
    if not healthy:
        started = False
        if not _is_running_in_container():
            started = _try_start_pymol_interactive_service()
            if started:
                healthy = _service_healthy()

        if not healthy:
            print("\nℹ️  Visualization is ready, but pymol_interactive is not reachable.")
            print("   Start it with: docker compose --profile interactive up -d pymol-interactive")
            print(f"   Then open: {no_vnc_url}")
            return

    try:
        response = requests.post(
            f"{base_url}/api/load_job",
            json={"job_id": job_id},
            timeout=15,
        )
        if response.status_code != 200:
            print("\n⚠️  pymol_interactive is running, but failed to auto-load job.")
            print(f"   API response: {response.text}")
            print(f"   Open noVNC: {no_vnc_url}")
            return

        print("\n✅ Interactive PyMOL session is ready.")
        print(f"   Open noVNC: {no_vnc_url}")
        print(f"   Loaded job: {job_id}")
        print("   To derive docking box from your selection (e.g., 'sele'):")
        print(f"   POST {base_url}/api/box/from_selection with {{\"job_id\":\"{job_id}\",\"selection\":\"sele\"}}")
    except requests.RequestException as exc:
        print(f"\n⚠️  Could not contact pymol_interactive API: {exc}")
        print(f"   Open noVNC: {no_vnc_url}")


def process_workflow(
    user_input: str,
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    clarification_mode: str = CLARIFICATION_MODE_HYBRID,
    session: Optional[InteractiveSession] = None,
) -> int:
    """
    Process a workflow from natural language input.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        print("\n" + "="*80)
        print("Processing workflow request...")
        print("="*80)
        print(f"\n📝 Input: {user_input}\n")
        
        # Parse with LLM
        print("🤖 Parsing with LLM...")
        parsed = parse_workflow_with_llm(llm_client, user_input)
        
        # Validate and enrich
        print("✅ Validating workflow...")
        workflow = parser_obj.validate_workflow(parsed)

        # Persist workflow parameters immediately after parsing/validation.
        shared_job_id = _generate_job_id()
        state_manager = WorkflowStateManager(orchestrator.output_base / shared_job_id)
        parser_obj.set_state_manager(state_manager)
        state_manager.save_parameters_checkpoint(workflow.get("parameters", {}), source="initial_parse")
        
        # Show summary
        print("\n" + parser_obj.get_workflow_summary())
        

        clarification_mode = _normalize_clarification_mode(clarification_mode)

        # Check for missing parameters
        if parser_obj.has_missing_parameters():
            print("\n⚠️  I need a bit more information before running this workflow.")
            print(parser_obj.format_missing_prompt())

            # Ask only for missing/invalid fields, one short question at a time.
            parser_obj._live_box_provider = lambda: _create_box_via_live_pymol(
                orchestrator,
                parser_obj.parsed_workflow,
                shared_job_id,
                state_manager=state_manager,
            )
            try:
                if clarification_mode == CLARIFICATION_MODE_HYBRID:
                    _collect_missing_parameters_hybrid(
                        parser_obj,
                        str(orchestrator.output_base),
                        llm_client=llm_client,
                    )
                else:
                    _collect_missing_parameters(parser_obj, str(orchestrator.output_base))
            finally:
                parser_obj._live_box_provider = None

            if parser_obj.has_missing_parameters():
                print("\n❌ Could not resolve all required parameters.")
                print(parser_obj.format_missing_prompt())
                print("Try clarifying your request or run with --clarification-mode legacy.")
                return 1

            workflow = parser_obj.parsed_workflow
            
            print("\n" + parser_obj.get_workflow_summary())
        
        # Confirm execution
        print("\n" + "="*80)
        confirm = input("Execute this workflow? [Y/n]: ").strip().lower()
        if confirm and confirm not in ['y', 'yes']:
            print("Workflow cancelled.")
            return 0
        
        # Execute workflow
        print("\n" + "="*80)
        print("Executing workflow...")
        print("="*80 + "\n")
        
        latest_parameters = state_manager.load_latest_parameters()
        if isinstance(latest_parameters, dict):
            workflow["parameters"] = latest_parameters

        results = orchestrator.execute_workflow(
            workflow,
            job_id=shared_job_id,
            state_manager=state_manager,
        )
        
        # Show results
        print("\n" + "="*80)
        print("WORKFLOW RESULTS")
        print("="*80)
        print(f"\nJob ID: {results['job_id']}")
        print(f"Status: {results['status']}")
        print(f"Services Executed: {', '.join(results['services_executed'])}")
        
        if results.get("errors"):
            print("\nErrors:")
            for error in results["errors"]:
                print(f"  ❌ {error}")
            return 1
        
        print("\n✅ Workflow completed successfully!")
        print(f"\nResults saved to: {orchestrator.output_base / results['job_id']}")

        if session is not None:
            session.activate(results["job_id"], state_manager, last_results=results)

        _integrate_with_pymol_interactive(results)
        
        return 0
        
    except Exception as e:
        logger.error(f"Failed to process workflow: {e}", exc_info=True)
        print(f"\n❌ Failed to process workflow: {e}")
        return 1
    finally:
        parser_obj.set_state_manager(None)


if __name__ == "__main__":
    sys.exit(main())
