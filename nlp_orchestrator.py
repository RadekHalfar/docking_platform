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
from datetime import datetime, timezone
from typing import Optional, List, Callable, Dict, Any, Set

from services.llm_orchestrator import (
    BaseLLMClient,
    WorkflowOrchestrator,
    WorkflowParser,
    WorkflowStateManager,
    InteractiveSession,
    compute_rerun_steps,
    describe_rerun_plan,
    create_llm_client,
    parse_workflow_with_llm,
    generate_llm_follow_up_question,
    extract_updates_with_llm,
    extract_box_source_choice_with_llm,
    classify_session_intent,
    extract_updates_from_command_with_llm,
    save_results_bundle,
    validate_bundle_name,
)
from services.llm_orchestrator.workflow_llm_parser import _deterministic_intent
from services.llm_orchestrator.orchestrator import VERBOSITY_LOW, VERBOSITY_NORMAL, VERBOSITY_MCP_DEMO
from common.utils import generate_job_id

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
    print("  - <request>                  # Start first workflow (no active job yet)")
    print("  - new <request>              # Start a fresh workflow in current session")
    print("  - change ligand to HEM       # Update parameters (natural language)")
    print("  - set center to 10,20,30     # Update docking box center")
    print("  - state / show state         # Show active job state from disk")
    print("  - end / quit / exit          # End session")
    print("  - help                       # Show this help")
    print("  - run / dock / rerun         # Execute (or re-execute) workflow steps")
    print("  - save results as <name>     # Save named result bundle")
    print("  - save docking as <name>     # Save docking-only bundle")
    print("  - save visualization as <name>  # Save visualization-only bundle")
    print("  - history                    # Show revision history")
    print("  - undo <revision>            # Restore parameters/state to a prior revision")
    print("\nComing soon (stubs active):")
    print("  - use pymol box              # Define box from PyMOL selection (Phase 6)")


def _handle_update_parameters(
    user_input: str,
    llm_client: "BaseLLMClient",
    session: "InteractiveSession",
) -> Set[str]:
    """Extract parameter updates from a natural language command and persist them.

    Returns the set of changed parameter keys (empty if nothing changed).
    """
    updates = extract_updates_from_command_with_llm(llm_client, user_input)
    if not updates:
        print("Could not extract any parameter updates from that command.")
        print("Examples: 'change ligand to HEM', 'set center to 10,20,30'")
        return set()

    print("Parameter updates:")
    for key, value in updates.items():
        print(f"  {key}: {value}")

    if session.state_manager is not None:
        current = session.state_manager.load_latest_parameters() or {}
        old_params = dict(current)
        current.update(updates)
        session.state_manager.save_parameters_checkpoint(current, source="interactive_update")
        print("\u2705 Parameters saved to disk.")

        # Compute what changed and offer partial rerun.
        changed_keys = {k for k in updates if old_params.get(k) != updates[k]}

        # Keep docking_box.json on disk in sync whenever box coordinates change
        # so that every subsequent read of docking_box.json reflects the current
        # state (e.g. save_results_bundle, PyMOL reload, debug inspection).
        _BOX_PARAMS = {"center", "box_size"}
        if changed_keys & _BOX_PARAMS and current.get("center") and current.get("box_size"):
            from common.file_io import atomic_write_json as _write_json
            _box_payload: Dict[str, Any] = {
                "job_id": session.state_manager.job_dir.name,
                "center": current["center"],
                "box_size": current["box_size"],
                "source": "interactive_update",
            }
            _write_json(session.state_manager.job_dir / "docking_box.json", _box_payload)
            logger.debug("Updated docking_box.json from interactive parameter change.")

        return changed_keys
    else:
        print("\u26a0\ufe0f  No active state manager — parameters could not be persisted.")
        return set()


def _execute_partial_rerun(
    session: "InteractiveSession",
    orchestrator: WorkflowOrchestrator,
    changed_params: Set[str],
) -> Optional[Dict[str, Any]]:
    """Compute and execute a dependency-aware partial rerun.

    Returns the execution results dict, or None if the user cancelled or
    no steps needed re-execution.
    """
    if not session.has_active_job() or session.state_manager is None:
        print("No active job to rerun.")
        return None

    # Determine which services were originally executed.
    original_services = (session.last_results or {}).get("services_executed", [])
    if not original_services:
        print("Cannot determine original services; use 'new <request>' instead.")
        return None

    steps = compute_rerun_steps(changed_params, original_services=original_services)
    if not steps:
        print("No steps need to be re-executed for the changed parameters.")
        return None

    print(f"\n{describe_rerun_plan(steps)}")

    confirm = input("Execute partial rerun? [Y/n]: ").strip().lower()
    if confirm and confirm not in ("y", "yes"):
        print("Partial rerun cancelled.")
        return None

    # Build a workflow containing only the steps to rerun.
    latest_params = session.state_manager.load_latest_parameters() or {}
    rerun_workflow: Dict[str, Any] = {
        "services": steps,
        "parameters": latest_params,
    }

    print("\n" + "=" * 80)
    print("Executing partial rerun...")
    print("=" * 80 + "\n")

    results = orchestrator.execute_workflow(
        rerun_workflow,
        job_id=session.job_id,
        state_manager=session.state_manager,
    )

    # Update session with new results.
    merged_results = dict(session.last_results or {})
    merged_results["services_executed"] = original_services  # keep full list
    merged_results["status"] = results.get("status", "failed")
    merged_results["errors"] = results.get("errors", [])
    for key, value in results.get("results", {}).items():
        merged_results.setdefault("results", {})[key] = value
    session.last_results = merged_results

    print(f"\nPartial rerun status: {results.get('status', 'unknown')}")
    if results.get("errors"):
        for err in results["errors"]:
            print(f"  ❌ {err}")
    else:
        print("✅ Partial rerun completed successfully!")

    return results


def _handle_show_history(session: "InteractiveSession") -> None:
    """Print the revision history for the active job."""
    if session.state_manager is None:
        print("No active job — no history available.")
        return

    revisions = session.state_manager.list_revisions()
    if not revisions:
        print("No revisions recorded yet.")
        return

    print(f"\nRevision history for job {session.job_id}:")
    print(f"  {'Rev':>5}  {'Timestamp':26}  {'Type':22}  {'Details'}")
    print("  " + "-" * 80)
    for r in revisions:
        ts = r["timestamp"][:26] if r["timestamp"] else ""
        etype = r["event_type"]
        details = r["details"]
        detail_str = ", ".join(f"{k}={v}" for k, v in details.items())
        flags = []
        if r["has_parameters"]:
            flags.append("params")
        if r["has_state"]:
            flags.append("state")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {r['revision']:>5}  {ts}  {etype:<22}  {detail_str}{flag_str}")
    print(f"\n  Current revision: {session.state_manager.get_current_revision()}")
    print("  Use 'undo <revision>' to restore any prior revision.")


def _handle_undo_revision(
    rev_str: Optional[str],
    session: "InteractiveSession",
) -> None:
    """Restore a prior revision and suggest a follow-up rerun."""
    if session.state_manager is None:
        print("No active job — cannot undo.")
        return

    # Resolve revision number.
    revision: Optional[int] = None
    if rev_str:
        try:
            revision = int(rev_str)
        except ValueError:
            pass

    if revision is None:
        try:
            raw = input("Revision number to restore: ").strip()
            revision = int(raw)
        except (ValueError, EOFError, KeyboardInterrupt):
            print("Invalid or no revision number provided. Undo cancelled.")
            return

    try:
        data = session.state_manager.restore_revision(revision)
    except ValueError as exc:
        print(f"❌ {exc}")
        return

    print(f"\n✅ Revision {revision} restored.")

    # Report what was restored.
    if data["parameters"] is not None:
        params = data["parameters"]
        print("  Parameters restored:")
        for k in ("protein_id", "ligand_ids", "center", "box_size"):
            if k in params:
                print(f"    {k}: {params[k]}")
    else:
        print("  No parameter checkpoint at that revision — parameters unchanged.")

    if data["runtime_state"] is not None:
        keys = sorted(data["runtime_state"].keys())
        print(f"  Runtime state restored ({', '.join(keys)}).")
    else:
        print("  No state checkpoint at that revision — runtime state unchanged.")

    # Mark all params as changed so 'rerun' will execute the full necessary set.
    if data["parameters"] is not None:
        session.pending_changed_params |= {"protein_id", "ligand_ids", "center", "box_size"}
        print("\n  ⚠️  Parameters changed. Type 'rerun' to re-execute affected steps.")


def _handle_save_results(
    intent_result: Dict[str, Any],
    session: "InteractiveSession",
) -> None:
    """Execute the save_results intent: resolve name/scope, copy artifacts, write metadata."""
    if session.state_manager is None:
        print("No active job state manager — cannot save results.")
        return

    if session.pending_changed_params:
        changed = ", ".join(sorted(session.pending_changed_params))
        print(f"❌ Cannot save: parameters changed since last docking ({changed}).")
        print("   Run 'rerun' first, or 'undo' to revert parameters.")
        return

    job_dir = session.state_manager.job_dir
    scope: str = (intent_result or {}).get("scope") or "all"
    name: Optional[str] = (intent_result or {}).get("name")

    # Prompt for name when not supplied in the command.
    if not name:
        try:
            name = input("Enter a name for this result bundle: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSave cancelled.")
            return
        if not name:
            print("No name provided. Save cancelled.")
            return

    # Validate before any filesystem work.
    err = validate_bundle_name(name)
    if err:
        print(f"❌ Invalid bundle name: {err}")
        return

    revision = session.state_manager.get_current_revision()
    parameters = session.state_manager.load_latest_parameters() or {}

    # First attempt without overwrite.
    result = save_results_bundle(job_dir, name, scope, revision, parameters, overwrite=False)

    if not result["success"] and result["error"] == "exists":
        print(f"A bundle named '{name}' already exists.")
        try:
            choice = input("Overwrite? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSave cancelled.")
            return
        if choice not in ("y", "yes"):
            # Offer auto-suffix fallback.
            suffix = datetime.now(timezone.utc).strftime("%H%M%S")
            new_name = f"{name}_{suffix}"
            print(f"Saving as '{new_name}' instead.")
            result = save_results_bundle(
                job_dir, new_name, scope, revision, parameters, overwrite=False
            )
        else:
            result = save_results_bundle(
                job_dir, name, scope, revision, parameters, overwrite=True
            )

    if result["success"]:
        arts = result["artifacts"]
        print(f"\n✅ Saved bundle '{name}' (scope: {scope})")
        print(f"   Path: {result['bundle_path']}")
        if arts:
            print(f"   Artifacts: {', '.join(arts)}")
        else:
            print("   ⚠️  No artifacts were available to copy into the bundle.")
    else:
        print(f"❌ Save failed: {result['error']}")


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

            # Unified intent dispatch — deterministic patterns checked first,
            # LLM fallback only when an active job exists and pattern didn't match.
            intent_result = classify_session_intent(llm_client, user_input) if session.has_active_job() else None
            # When no active job, still honour deterministic commands.
            if intent_result is None:
                intent_result = _deterministic_intent(user_input)
            intent = (intent_result or {}).get("intent")

            if intent == "end_session":
                session.session_active = False
                print("Goodbye!")
                break

            if intent == "help":
                _print_interactive_help()
                continue

            if intent == "show_state":
                print("\n" + session.format_state_summary())
                continue

            if intent == "show_history":
                _handle_show_history(session)
                continue

            if intent == "new_workflow":
                new_req = user_input[4:].strip() if user_input.lower().startswith("new ") else user_input.strip()
                if not new_req:
                    print("Please provide a workflow request.")
                else:
                    process_workflow(
                        new_req,
                        llm_client,
                        orchestrator,
                        parser_obj,
                        clarification_mode=clarification_mode,
                        session=session,
                    )
                continue

            # Intents below require an active job.
            if intent and intent != "unknown" and not session.has_active_job():
                print("No active job yet. Start a workflow first.")
                continue

            if intent == "update_parameters":
                changed = _handle_update_parameters(user_input, llm_client, session)
                if changed:
                    session.pending_changed_params |= changed
                    print("\nParameters updated. Type 'rerun' or 'dock' to re-execute affected steps.")
                continue

            if intent == "rerun_steps":
                original = (session.last_results or {}).get("services_executed", [])
                if original:
                    rerun_params = session.pending_changed_params if session.pending_changed_params else {"protein_id", "ligand_ids", "center", "box_size"}
                    _execute_partial_rerun(
                        session, orchestrator, changed_params=rerun_params,
                    )
                    session.pending_changed_params = set()
                else:
                    print("No previous workflow execution to rerun. Use 'new <request>' instead.")
                continue

            if intent == "save_results":
                _handle_save_results(intent_result or {}, session)
                continue

            if intent == "undo_revision":
                rev = (intent_result or {}).get("name")
                _handle_undo_revision(rev, session)
                continue

            if intent == "use_pymol_box":
                print("\U0001f52c PyMOL box selection coming in Phase 6.")
                continue

            # No recognised intent — if there's an active job, we don't know
            # what the user meant; otherwise treat as a new workflow request.
            if session.has_active_job():
                print("I didn't understand that command. Type 'help' to see supported commands.")
            else:
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
            logger.error(f"Error: {e}", exc_info=True)
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


def _print_clarification_state(parser_obj: WorkflowParser) -> None:
    """Print current workflow parameters while clarification is in progress."""
    workflow = parser_obj.parsed_workflow or {"parameters": {}, "missing_required": []}
    params = workflow.get("parameters", {})
    missing = parser_obj.get_missing_parameters() if parser_obj.has_missing_parameters() else []

    print("\nCurrent clarification state:")
    print(f"  parameters: {params}")
    print(f"  missing_required: {missing}\n")


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

            if source_answer.lower() in {"state", "show state"}:
                _print_clarification_state(parser_obj)
                turn -= 1
                continue

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

        if user_answer.lower() in {"state", "show state"}:
            _print_clarification_state(parser_obj)
            turn -= 1
            continue

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
            if response.status_code != 200:
                return False

            try:
                payload = response.json()
            except ValueError:
                return False

            return payload.get("pymol_rpc") is True
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
        print("pymol_interactive is not ready.")
        print("The helper API responded, but the PyMOL GUI/RPC backend is not available yet.")
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
    last_load_error = ""
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
            last_load_error = response.text
        except requests.RequestException:
            pass
        time.sleep(1)

    if not loaded:
        print("Could not auto-load prepared structures into interactive PyMOL.")
        if last_load_error:
            print(f"API response: {last_load_error}")
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
    except (OSError, subprocess.SubprocessError):
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
        shared_job_id = generate_job_id()
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
            # Activate session so parameter-update commands retain context.
            if session is not None:
                session.activate(
                    shared_job_id,
                    state_manager,
                    last_results={
                        "status": "cancelled",
                        "services_executed": workflow.get("services", []),
                        "results": {},
                    },
                )
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
