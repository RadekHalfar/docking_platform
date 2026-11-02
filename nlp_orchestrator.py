"""
LLM Orchestrator CLI

Command-line interface for natural language workflow orchestration.
"""
import sys
import os
import logging
import argparse
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

from services.llm_orchestrator import (
    BaseLLMClient,
    WorkflowOrchestrator,
    WorkflowParser,
    WorkflowStateManager,
    InteractiveSession,
    compute_rerun_steps,
    describe_rerun_plan,
    missing_required_params,
    create_llm_client,
    is_llm_configured,
    NO_LLM_PROVIDER,
    parse_workflow_with_llm,
    generate_llm_follow_up_question,
    extract_updates_with_llm,
    classify_session_intent,
    extract_updates_from_command_with_llm,
    split_command_sequence,
    _llm_split_command_sequence,
    save_results_bundle,
    timestamped_bundle_name,
    validate_bundle_name,
    compare_jobs,
    find_prunable_jobs,
    delete_jobs,
)
from services.llm_orchestrator.workflow_llm_parser import _deterministic_intent
from services.llm_orchestrator.workflow_definition import CORE_PARAMS, WORKFLOW_STEPS
from services.llm_orchestrator.workflow_state_manager import describe_revision
from services.llm_orchestrator.interactive_session import compute_changed_params, resolve_prepared_protein_pdb_path
from services.llm_orchestrator.orchestrator import VERBOSITY_LOW, VERBOSITY_NORMAL, VERBOSITY_MCP_DEMO
from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM, docking_algorithm_label, normalize_docking_algorithm
from common.pdb_geometry import compute_protein_center_and_box
from common.path_utils import from_container_path
from common.utils import generate_job_id
from common.file_io import atomic_write_json

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
        return True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CLARIFICATION_MODE_HYBRID = "hybrid"

# Printed when a natural-language command arrives with no LLM set up
# (provider "none", the Phase 18 default).
NO_LLM_MESSAGE = (
    "No LLM provider is configured, and natural-language commands need one.\n"
    "Pick one of:\n"
    "  - a cloud provider: --llm-provider openai|anthropic|metacentrum --llm-model <model> --llm-api-key <key>\n"
    "    (or set LLM_PROVIDER, LLM_MODEL and LLM_API_KEY in .env)\n"
    "  - a local Ollama: docker compose --profile local-llm up -d ollama\n"
    "    then --llm-provider ollama --llm-model gemma3:4b (the model downloads on first use)\n"
    "The GUI (http://localhost:3000) runs docking from its forms without an LLM."
)
# Printed when a provider is set but no model (LLM_MODEL defaults to empty
# since Phase 18); the GUI's Apply refuses the same (Review Stage R5).
NO_MODEL_MESSAGE = (
    "LLM provider '{provider}' has no model set. Pass --llm-model <model> or set "
    "LLM_MODEL in .env (--list-models shows an Ollama's models)."
)
CLARIFICATION_MODE_LEGACY = "legacy"

# Add verbosity filter to root logger
verbosity_filter = VerbosityFilter()
for handler in logging.getLogger().handlers:
    handler.addFilter(verbosity_filter)

# Parameters that, when changed, require docking_box.json to be refreshed on disk.
_BOX_PARAMS: frozenset = frozenset({"center", "box_size"})


def _print_interactive_help() -> None:
    """Print supported interactive session commands."""
    print("\nSupported commands:")
    print("  - <request>                  # Start first workflow (no active job yet)")
    print("  - new <request>              # Start a fresh workflow in current session")
    print("  - change ligand to HEM       # Update parameters (natural language)")
    print("  - set center to 10,20,30     # Update docking box center")
    print("  - set up box center as protein center  # Center box on the loaded protein")
    print("  - look across all protein    # Size box to cover the whole protein (+10%)")
    print("  - use vina / use moldina     # Switch docking algorithm (Vina Monte Carlo | Moldina PSO)")
    print("  - dock with vina             # Switch algorithm and redock (dock + visualize only)")
    print("  - search ligand aspirin       # Search RCSB ligands by name and choose one")
    print("  - state / show state         # Show active job state from disk")
    print("  - end / quit / exit          # End session")
    print("  - help                       # Show this help")
    print("  - run / dock / rerun         # Execute (or re-execute) workflow steps")
    print("  - save results as <name>     # Save named result bundle")
    print("  - save docking as <name>     # Save docking-only bundle")
    print("  - save visualization as <name>  # Save visualization-only bundle")
    print("  - history                    # Show revision history")
    print("  - undo <revision>            # Restore parameters/state to a prior revision")


def _persist_parameter_updates(
    updates: Dict[str, Any],
    session: "InteractiveSession",
) -> Set[str]:
    """Merge *updates* onto the session's last-checkpointed parameters, save
    a new checkpoint revision, and keep docking_box.json in sync.

    Returns the set of keys that actually changed (empty if none did, or if
    there's no active state manager to persist into). Shared by
    :func:`_handle_update_parameters` (LLM-extracted updates from a
    free-text command) and :func:`_handle_set_protein_geometry` (updates
    computed from the loaded protein's bounding box) -- one definition of
    "apply and persist a parameter update" instead of two.
    """
    if session.state_manager is None:
        print("\u26a0\ufe0f  No active state manager — parameters could not be persisted.")
        return set()

    current = session.state_manager.load_latest_parameters() or {}
    old_params = dict(current)
    current.update(updates)
    session.state_manager.save_parameters_checkpoint(current, source="interactive_update")
    print("✅ Parameters saved to disk.")

    # Compute what changed and offer partial rerun -- the shared diff, so a
    # key an old checkpoint lacks (docking_algorithm before Phase 15) reads
    # as its default, not as a change (Review Stage R1, Phase 15).
    changed_keys = compute_changed_params(old_params, current)

    # Keep docking_box.json on disk in sync whenever box coordinates change
    # so that every subsequent read of docking_box.json reflects the current
    # state (e.g. save_results_bundle, the GUI viewer, debug inspection).
    if changed_keys & _BOX_PARAMS and current.get("center") and current.get("box_size"):
        _box_payload: Dict[str, Any] = {
            "job_id": session.state_manager.job_dir.name,
            "center": current["center"],
            "box_size": current["box_size"],
            "source": "interactive_update",
        }
        atomic_write_json(session.state_manager.job_dir / "docking_box.json", _box_payload)
        logger.debug("Updated docking_box.json from interactive parameter change.")

    return changed_keys


def _handle_update_parameters(
    user_input: str,
    llm_client: "BaseLLMClient",
    session: "InteractiveSession",
) -> Set[str]:
    """Extract parameter updates from a natural language command and persist them.

    Returns the set of changed parameter keys (empty if nothing changed).
    """
    updates = extract_updates_from_command_with_llm(llm_client, user_input)
    # The LLM's value is never trusted as-is: an unrecognised algorithm
    # would only fail later inside moldina-runner.
    #
    # NOTE (Review Stage R1, Phase 15, altitude finding #4, deferred): this
    # per-key clause exists because this write path (and
    # _persist_parameter_updates) bypasses WorkflowParser's validation gate,
    # which already normalises and rejects algorithms; the other
    # LLM-extracted fields here go unvalidated. Fix: a parser-level
    # normalize_parameter_updates(updates) -> (clean, issues) built on
    # get_parameter_issue, called from _persist_parameter_updates, replacing
    # this clause. Trigger: the next enum- or alias-valued parameter, or
    # the next LLM-extracted field that needs validation on this path. See
    # dev_plan.md Phase 15 Future Work and docs/reviews/stage_15_r1.md.
    if "docking_algorithm" in updates:
        raw_algorithm = updates.pop("docking_algorithm")
        algorithm = normalize_docking_algorithm(raw_algorithm)
        if algorithm is None:
            print(_unrecognised_algorithm_message(raw_algorithm))
        else:
            updates["docking_algorithm"] = algorithm
    if not updates:
        print("Could not extract any parameter updates from that command.")
        print("Examples: 'change ligand to HEM', 'set center to 10,20,30'")
        return set()

    print("Parameter updates:")
    for key, value in updates.items():
        print(f"  {key}: {value}")

    return _persist_parameter_updates(updates, session)


def _handle_set_protein_geometry(
    target: Optional[str],
    session: "InteractiveSession",
    orchestrator: WorkflowOrchestrator,
) -> Set[str]:
    """Compute center/box_size from the loaded protein's own bounding box
    and persist it -- the "set up box center as protein center" / "look
    across all protein" commands.

    Returns the set of changed parameter keys (empty on any no-op: no
    protein loaded yet, the prepared file is gone, or the computation
    failed) -- mirrors :func:`_handle_update_parameters`'s own "could not
    extract" branch, an informational no-op rather than a chain-halting
    failure.
    """
    raw_path = resolve_prepared_protein_pdb_path(session.last_results)
    if not raw_path:
        print("No prepared protein yet. Run a workflow through protein preparation first, then ask again.")
        return set()

    # The path is as the protein_preparer MCP service's own container sees
    # it (e.g. "/app/orchestrator_output/..."); translate to this process's
    # own local mount of the same shared volume before reading it directly.
    local_path = from_container_path(raw_path, orchestrator.output_base)
    if not local_path.exists():
        print(f"Prepared receptor no longer on disk: {local_path}")
        return set()

    try:
        pdb_text = local_path.read_text(encoding="utf-8", errors="replace")
        center, box_size = compute_protein_center_and_box(pdb_text)
    except (OSError, ValueError) as exc:
        print(f"Could not compute protein geometry: {exc}")
        return set()

    if target == "center":
        updates: Dict[str, Any] = {"center": center}
        print(f"Protein center: {center}")
    else:
        updates = {"box_size": box_size}
        print(f"Box size to cover the whole protein (+10% margin): {box_size}")

    return _persist_parameter_updates(updates, session)


def _unrecognised_algorithm_message(name: Any) -> str:
    return f"Docking algorithm '{name}' is not recognised. Use 'moldina' (PSO) or 'vina' (Monte Carlo)."


def _handle_set_docking_algorithm(
    algorithm: str,
    session: "InteractiveSession",
) -> Set[str]:
    """Persist a ``docking_algorithm`` switch ("use vina", "dock with
    moldina"). *algorithm* is canonical (normalised by the caller). Returns
    the changed keys -- empty when the session already uses *algorithm* (or
    nothing could be persisted)."""
    label = docking_algorithm_label(algorithm)
    if session.state_manager is not None:
        current = (session.state_manager.load_latest_parameters() or {}).get(
            "docking_algorithm", DEFAULT_DOCKING_ALGORITHM
        )
        if current == algorithm:
            print(f"Docking algorithm is already {label}.")
            return set()
    print(f"Docking algorithm: {label}")
    return _persist_parameter_updates({"docking_algorithm": algorithm}, session)


def _rerun_pending(
    session: "InteractiveSession",
    orchestrator: WorkflowOrchestrator,
    extra: Optional[Set[str]] = None,
) -> bool:
    """Rerun the steps affected by the pending parameter changes (plus
    *extra*), or every core step if nothing is pending -- the ``rerun``
    command. Returns False when there was nothing to rerun or the rerun
    was cancelled."""
    last = session.last_results or {}
    if not (last.get("services_executed") or last.get("services_planned")):
        print("No previous workflow execution to rerun. Use 'new <request>' instead.")
        return False
    rerun_params = session.pending_changed_params | (extra or set())
    result = _execute_partial_rerun(
        session, orchestrator, changed_params=rerun_params or CORE_PARAMS,
    )
    session.pending_changed_params = set()
    return result is not None


def _mark_changed_and_hint_rerun(
    session: "InteractiveSession", changed: Set[str], prefix: str = ""
) -> None:
    """If *changed* is non-empty, record it for the next rerun and print a
    hint. Shared tail of the ``update_parameters``/``set_protein_geometry``
    branches in :func:`_dispatch_intent` -- both edit parameters via
    :func:`_persist_parameter_updates` and need the same "nothing to do on
    a no-op" / "tell the user to rerun" follow-up, differing only in an
    optional leading confirmation phrase.
    """
    if changed:
        session.pending_changed_params |= changed
        print(f"\n{prefix}Type 'rerun' or 'dock' to re-execute affected steps.")


def _handle_search_ligand(
    orchestrator: WorkflowOrchestrator,
    session: "InteractiveSession",
    query_name: Optional[str],
) -> bool:
    """Search ligands by name, let user select one, and store it in session parameters."""
    if session.state_manager is None:
        print("No active workflow state found. Start a workflow first.")
        return False

    query = (query_name or "").strip()
    if not query:
        query = input("Ligand name to search: ").strip()
    if not query:
        print("No search query provided.")
        return False

    try:
        result = orchestrator.call_mcp_tool(
            orchestrator.services["rcsb_downloader"],
            "search_ligands",
            {"name": query, "max_results": 10},
        )
    except Exception as exc:
        print(f"❌ Ligand search failed: {exc}")
        return False

    candidates = result.get("candidates", []) if isinstance(result, dict) else []
    if not candidates:
        print(f"No ligands found for '{query}'.")
        return False

    print(f"\nFound {len(candidates)} ligand candidate(s) for '{query}':")
    for idx, cand in enumerate(candidates, start=1):
        comp_id = str(cand.get("component_id", "")).strip() or "?"
        name = str(cand.get("name", "")).strip() or "(no name)"
        score = cand.get("score")
        score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
        print(f"  {idx}. {comp_id} - {name}{score_text}")

    selection = input(
        "Choose ligand by index or component ID (blank to cancel): "
    ).strip()
    if not selection:
        print("Search cancelled.")
        return False

    selected_id: Optional[str] = None
    if selection.isdigit():
        index = int(selection)
        if 1 <= index <= len(candidates):
            selected_id = str(candidates[index - 1].get("component_id", "")).strip().upper() or None
    else:
        selected_id = selection.upper()

    if not selected_id:
        print("Invalid ligand selection.")
        return False

    latest = session.state_manager.load_latest_parameters() or {}
    latest["ligand_ids"] = [selected_id]
    session.state_manager.save_parameters_checkpoint(latest, source="search_ligand")
    session.pending_changed_params |= {"ligand_ids"}

    print(f"✅ Selected ligand: {selected_id}")
    print("Type 'rerun' to continue workflow with the selected ligand.")
    return True


def _is_probable_chem_comp_id(value: str) -> bool:
    """Return True when *value* resembles an RCSB chem_comp identifier."""
    token = (value or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,4}", token))


def _search_and_select_ligand_id(
    orchestrator: WorkflowOrchestrator,
    query_name: str,
) -> Optional[str]:
    """Search ligands by name and return a selected component ID."""
    query = (query_name or "").strip()
    if not query:
        return None

    try:
        result = orchestrator.call_mcp_tool(
            orchestrator.services["rcsb_downloader"],
            "search_ligands",
            {"name": query, "max_results": 10},
        )
    except Exception as exc:
        print(f"❌ Ligand search failed for '{query}': {exc}")
        return None

    candidates = result.get("candidates", []) if isinstance(result, dict) else []
    if not candidates:
        print(f"No ligands found for '{query}'.")
        return None

    print(f"\nFound {len(candidates)} ligand candidate(s) for '{query}':")
    for idx, cand in enumerate(candidates, start=1):
        comp_id = str(cand.get("component_id", "")).strip() or "?"
        name = str(cand.get("name", "")).strip() or "(no name)"
        score = cand.get("score")
        score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
        print(f"  {idx}. {comp_id} - {name}{score_text}")

    selection = input(
        "Choose ligand by index or component ID (blank to cancel workflow): "
    ).strip()
    if not selection:
        return None

    if selection.isdigit():
        idx = int(selection)
        if 1 <= idx <= len(candidates):
            return str(candidates[idx - 1].get("component_id", "")).strip().upper() or None
        return None

    return selection.upper()


def _auto_resolve_named_ligands_for_workflow(
    orchestrator: WorkflowOrchestrator,
    workflow: Dict[str, Any],
) -> bool:
    """Resolve non-code ligand tokens via mandatory interactive search-and-selection."""
    params = workflow.get("parameters", {}) if isinstance(workflow, dict) else {}
    ligands = params.get("ligand_ids")
    if not isinstance(ligands, list) or not ligands:
        return True

    tokens = [str(x).strip() for x in ligands if str(x).strip()]
    unresolved_names = [tok for tok in tokens if not _is_probable_chem_comp_id(tok)]
    if not unresolved_names:
        return True

    name_to_selected: Dict[str, str] = {}
    for ligand_name in unresolved_names:
        key = ligand_name.lower()
        if key in name_to_selected:
            continue

        print(
            f"\nDetected ligand name '{ligand_name}' in workflow input. "
            "You must choose a specific RCSB ligand before execution."
        )
        selected = _search_and_select_ligand_id(orchestrator, ligand_name)
        if not selected:
            print("Workflow cancelled: ligand selection was not completed.")
            return False
        name_to_selected[key] = selected

    params["ligand_ids"] = [
        tok.upper() if _is_probable_chem_comp_id(tok) else name_to_selected[tok.lower()]
        for tok in tokens
    ]
    workflow["parameters"] = params
    return True


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

    # Determine which services were originally executed. Steps that were
    # planned but never ran (the first workflow was cancelled at its
    # confirmation prompt) must run whatever changed: they have no outputs
    # to reuse -- the CLI twin of the GUI's rule from Review Stage R1,
    # Phase 13 (Review Stage R1, Phase 15).
    last = session.last_results or {}
    executed = list(last.get("services_executed", []))
    unfinished = [s for s in last.get("services_planned", []) if s not in executed]
    if not executed and not unfinished:
        print("Cannot determine original services; use 'new <request>' instead.")
        return None

    planned = set(compute_rerun_steps(changed_params, original_services=executed)) | set(unfinished)
    steps = [s for s in WORKFLOW_STEPS if s in planned]
    if not steps:
        print("No steps need to be re-executed for the changed parameters.")
        return None

    # Pre-flight: verify all required parameters for the planned steps are present.
    latest_params = session.state_manager.load_latest_parameters() or {}
    missing = missing_required_params(steps, latest_params)
    if missing:
        print("\n❌ Cannot rerun — required parameters are missing:")
        for m in sorted(set(missing)):
            print(f"   • {m}")
        print("   Update them with a command like 'set protein to 6lu7' or restore a complete revision.")
        return None

    print(f"\n{describe_rerun_plan(steps)}")

    confirm = input("Execute partial rerun? [Y/n]: ").strip().lower()
    if confirm and confirm not in ("y", "yes"):
        print("Partial rerun cancelled.")
        return None

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
    # Deferred (R1, Stage 11.1 review, docs/reviews/stage_11_1_r1.md finding
    # #5): this hand-rolled merge duplicates the merge execute_workflow()
    # now does itself against workflow_results.json on disk, and could
    # diverge from it (e.g. `results` here misses a step that's actually on
    # disk, or vice versa after an out-of-band edit). Revisit if that
    # divergence is ever observed in practice, or next time this function is
    # touched -- trust `results["services_executed"]`/`results["results"]`
    # (already merged) instead of recomputing here.
    merged_results = dict(session.last_results or {})
    ran = set(executed) | set(results.get("services_executed", []))
    merged_results["services_executed"] = [s for s in WORKFLOW_STEPS if s in ran]  # keep full list
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
        print(f"  {r['revision']:>5}  {ts}  {r['event_type']:<22}  {describe_revision(r)}")
    # The parameters' revision, as the GUI shows it -- not
    # get_current_revision(), which also counts state_update events and so
    # named a revision whose parameters aren't the current ones.
    print(f"\n  Parameters are currently at revision {session.state_manager.get_parameters_revision()}.")
    print("  Use 'undo <revision>' to restore any prior revision.")


def _handle_undo_revision(
    rev_str: Optional[str],
    session: "InteractiveSession",
) -> bool:
    """Restore a prior revision and suggest a follow-up rerun.

    Returns True when a revision was successfully restored, False otherwise
    (no active job, invalid/missing revision number, or unknown revision).
    """
    if session.state_manager is None:
        print("No active job — cannot undo.")
        return False

    # Resolve revision number.
    revision: Optional[int] = None
    if rev_str:
        try:
            revision = int(rev_str)
        except ValueError:
            pass  # rev_str was not numeric; will prompt interactively below

    if revision is None:
        try:
            raw = input("Revision number to restore: ").strip()
            revision = int(raw)
        except (ValueError, EOFError, KeyboardInterrupt):
            print("Invalid or no revision number provided. Undo cancelled.")
            return False

    try:
        data = session.state_manager.restore_revision(revision)
    except ValueError as exc:
        print(f"❌ {exc}")
        return False

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
        session.pending_changed_params |= CORE_PARAMS
        print("\n  ⚠️  Parameters changed. Type 'rerun' to re-execute affected steps.")

    return True


def _handle_save_results(
    intent_result: Dict[str, Any],
    session: "InteractiveSession",
) -> bool:
    """Execute the save_results intent: resolve name/scope, copy artifacts, write metadata.

    Returns True when a bundle was successfully saved, False otherwise (no
    active job, unsaved parameter changes, cancelled/invalid name, or a
    filesystem save failure).
    """
    if session.state_manager is None:
        print("No active job state manager — cannot save results.")
        return False

    if session.pending_changed_params:
        changed = ", ".join(sorted(session.pending_changed_params))
        print(f"❌ Cannot save: parameters changed since last docking ({changed}).")
        print("   Run 'rerun' first, or 'undo' to revert parameters.")
        return False

    # The on-disk form of the check above (`pending` is per-process): the
    # latest parameters, e.g. a revision restored in the GUI or an earlier
    # CLI process, never ran, so the results on disk aren't theirs.
    if not session.state_manager.parameters_ran():
        print(
            f"❌ Cannot save: the parameters (revision {session.state_manager.get_parameters_revision()}) "
            "have not been run yet."
        )
        print("   Run 'rerun' first, or 'undo' to the revision the results came from.")
        return False

    job_dir = session.state_manager.job_dir
    scope: str = (intent_result or {}).get("scope") or "all"
    name: Optional[str] = (intent_result or {}).get("name")

    # Prompt for name when not supplied in the command.
    if not name:
        try:
            name = input("Enter a name for this result bundle: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSave cancelled.")
            return False
        if not name:
            print("No name provided. Save cancelled.")
            return False

    # Validate before any filesystem work.
    err = validate_bundle_name(name)
    if err:
        print(f"❌ Invalid bundle name: {err}")
        return False

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
            return False
        if choice not in ("y", "yes"):
            # Offer auto-suffix fallback.
            new_name = timestamped_bundle_name(job_dir, name)
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
        # The final name, which differs from *name* after the suffix fallback.
        print(f"\n✅ Saved bundle '{Path(result['bundle_path']).name}' (scope: {scope})")
        print(f"   Path: {result['bundle_path']}")
        if arts:
            print(f"   Artifacts: {', '.join(arts)}")
        else:
            print("   ⚠️  No artifacts were available to copy into the bundle.")
    else:
        print(f"❌ Save failed: {result['error']}")

    return bool(result["success"])


def _dispatch_intent(
    intent_result: Optional[Dict[str, Any]],
    user_input: str,
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    session: "InteractiveSession",
    clarification_mode: str,
    protein_file: Optional[str],
    ligand_files: Optional[List[str]],
    ligand_folder: Optional[str],
) -> bool:
    """Execute one already-classified session intent.

    Returns True on success/no-op, False on a failure that should halt a
    multi-intent chain (see ``_run_command_chain``). The single-intent path
    in ``_run_interactive_loop`` calls this too and simply ignores the
    return value, so single-intent behavior is unchanged.
    """
    intent = (intent_result or {}).get("intent")

    if intent == "end_session":
        session.session_active = False
        print("Goodbye!")
        return True

    if intent == "help":
        _print_interactive_help()
        return True

    if intent == "show_state":
        print("\n" + session.format_state_summary())
        return True

    if intent == "show_history":
        _handle_show_history(session)
        return True

    if intent == "new_workflow":
        parts = user_input.split(maxsplit=1)
        new_req = parts[1].strip() if len(parts) > 1 and user_input.lower().startswith("new ") else user_input.strip()
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
                protein_file=protein_file,
                ligand_files=ligand_files,
                ligand_folder=ligand_folder,
            )
        return True

    # Intents below require an active job.
    if intent and intent != "unknown" and not session.has_active_job():
        print("No active job yet. Start a workflow first.")
        return False

    if intent == "update_parameters":
        changed = _handle_update_parameters(user_input, llm_client, session)
        _mark_changed_and_hint_rerun(session, changed, prefix="Parameters updated. ")
        return True

    if intent == "set_protein_geometry":
        target = (intent_result or {}).get("name")
        changed = _handle_set_protein_geometry(target, session, orchestrator)
        _mark_changed_and_hint_rerun(session, changed)
        return True

    if intent == "set_docking_algorithm":
        # The deterministic detector only yields canonical names, but the LLM
        # classifier can return this intent too, with any "name".
        raw_name = (intent_result or {}).get("name")
        algorithm = normalize_docking_algorithm(raw_name)
        if algorithm is None:
            print(_unrecognised_algorithm_message(raw_name))
            return True
        changed = _handle_set_docking_algorithm(algorithm, session)
        if (intent_result or {}).get("rerun"):
            # "dock with vina": switch, then redock dock + visualize -- also
            # when the session already uses vina (nothing changed), which
            # would otherwise fall back to a full rerun.
            return _rerun_pending(session, orchestrator, extra={"docking_algorithm"})
        _mark_changed_and_hint_rerun(session, changed)
        return True

    if intent == "search_ligand":
        applied = _handle_search_ligand(
            orchestrator=orchestrator,
            session=session,
            query_name=(intent_result or {}).get("name"),
        )
        if applied and _ask_yes_no("Continue now with rerun of affected steps? [Y/n]: ", default_yes=True):
            # The shared rerun, so a session whose first workflow was
            # cancelled (nothing executed yet) runs its planned steps too.
            _rerun_pending(session, orchestrator, extra={"ligand_ids"})
        return True

    if intent == "rerun_steps":
        return _rerun_pending(session, orchestrator)

    if intent == "save_results":
        return _handle_save_results(intent_result or {}, session)

    if intent == "undo_revision":
        rev = (intent_result or {}).get("name")
        return _handle_undo_revision(rev, session)

    # No recognised intent — if there's an active job, we don't know
    # what the user meant; otherwise treat as a new workflow request.
    if session.has_active_job():
        print("I didn't understand that command. Type 'help' to see supported commands.")
        return False
    else:
        process_workflow(
            user_input,
            llm_client,
            orchestrator,
            parser_obj,
            clarification_mode=clarification_mode,
            session=session,
            protein_file=protein_file,
            ligand_files=ligand_files,
            ligand_folder=ligand_folder,
        )
        return True


def _run_command_chain(
    segments: List[str],
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    session: "InteractiveSession",
    clarification_mode: str,
    protein_file: Optional[str],
    ligand_files: Optional[List[str]],
    ligand_folder: Optional[str],
) -> None:
    """Classify and execute each segment of a multi-intent command in order.

    Stops at the first step that fails (or that ends the session), and
    prints a partial-completion summary explaining why.
    """
    total = len(segments)
    print(f"\nMulti-step command detected ({total} steps):")
    for i, segment in enumerate(segments, 1):
        print(f"  {i}. {segment}")

    if not _ask_yes_no(f"Execute these {total} steps in order? [Y/n]: ", default_yes=True):
        print("Multi-step command cancelled.")
        return

    completed = 0
    stop_reason: Optional[str] = None
    for i, segment in enumerate(segments, 1):
        print(f"\n[Step {i}/{total}] {segment}")
        intent_result = classify_session_intent(llm_client, segment)
        ok = _dispatch_intent(
            intent_result,
            segment,
            llm_client,
            orchestrator,
            parser_obj,
            session,
            clarification_mode,
            protein_file,
            ligand_files,
            ligand_folder,
        )
        if ok:
            completed += 1
        else:
            stop_reason = f'step {i} ("{segment}") failed'
        if not ok or not session.session_active:
            break

    summary = f"\nMulti-step command summary: {completed}/{total} steps completed."
    if stop_reason:
        summary += f" Stopped after {stop_reason}."
    print(summary)


def _run_interactive_loop(
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    clarification_mode: str,
    protein_file: Optional[str] = None,
    ligand_files: Optional[List[str]] = None,
    ligand_folder: Optional[str] = None,
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
            _prompt = f"🧬 [{session.job_id}] > " if session.has_active_job() else "🧬 > "
            user_input = input(_prompt).strip()

            if not user_input:
                continue

            # Multi-intent command sequencing (Phase 10) — only attempted for
            # follow-up commands in an active session; a 'new <request>' is
            # one atomic natural-language payload and must never be split.
            if session.has_active_job() and not re.match(r"^new\s+\S", user_input, re.IGNORECASE):
                segments = split_command_sequence(user_input)
                # "then" is a much rarer, stronger signal of intentional chaining
                # than bare "and" (which appears constantly in ordinary compound
                # parameter commands, e.g. "set center X and box Y") — only
                # spend an LLM call trying to recover a split for "then".
                if len(segments) == 1 and re.search(r"\bthen\b", user_input, re.IGNORECASE):
                    segments = _llm_split_command_sequence(llm_client, user_input)
                if len(segments) >= 2:
                    _run_command_chain(
                        segments,
                        llm_client,
                        orchestrator,
                        parser_obj,
                        session,
                        clarification_mode,
                        protein_file,
                        ligand_files,
                        ligand_folder,
                    )
                    continue
                if re.search(r"\bthen\b", user_input, re.IGNORECASE):
                    print(
                        "Note: treating this as a single command — chain complete "
                        "commands with 'and'/'then', e.g. 'state and save results as trial_a'."
                    )

            # Unified intent dispatch — deterministic patterns checked first,
            # LLM fallback only when an active job exists and pattern didn't match.
            intent_result = classify_session_intent(llm_client, user_input) if session.has_active_job() else None
            # When no active job, still honour deterministic commands.
            if intent_result is None:
                intent_result = _deterministic_intent(user_input)

            _dispatch_intent(
                intent_result,
                user_input,
                llm_client,
                orchestrator,
                parser_obj,
                session,
                clarification_mode,
                protein_file,
                ligand_files,
                ligand_folder,
            )
            if not session.session_active:
                break

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            print(f"❌ Error: {e}")

    return 0


def _format_comparison_table(comparison: Dict[str, Any]) -> str:
    """Format a compare_jobs() result as a fixed-width ASCII table."""
    ligands = comparison["ligands"]
    jobs = comparison["jobs"]
    affinities = comparison["affinities"]

    ligand_width = max([len("Ligand")] + [len(l) for l in ligands])
    job_widths = [max(len(j), 10) for j in jobs]

    header = f"{'Ligand':<{ligand_width}} " + " ".join(f"{j:<{w}}" for j, w in zip(jobs, job_widths))
    separator = "-" * ligand_width + " " + " ".join("-" * w for w in job_widths)

    lines = [header, separator]
    for ligand, row in zip(ligands, affinities):
        cells = []
        for val, w in zip(row, job_widths):
            cell = f"{val:.1f}" if isinstance(val, (int, float)) else "N/A"
            cells.append(f"{cell:<{w}}")
        lines.append(f"{ligand:<{ligand_width}} " + " ".join(cells))

    return "\n".join(lines)


def _run_compare_command(job_dirs: List[str]) -> int:
    """Handle `nlp_orchestrator.py compare JOB_DIR [JOB_DIR ...]`."""
    if not job_dirs:
        print("Usage: python nlp_orchestrator.py compare JOB_DIR [JOB_DIR ...]")
        return 1

    try:
        comparison = compare_jobs(job_dirs)
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1

    print(_format_comparison_table(comparison))
    return 0


def _run_cleanup_jobs_command(argv: List[str]) -> int:
    """Handle `nlp_orchestrator.py cleanup-jobs [options]`.

    orchestrator_output/ has no automatic retention -- job directories
    accumulate forever. This is a manual, opt-in tool for pruning old ones:
    dry-run by default (just lists what matches), nothing is deleted
    without --yes.
    """
    parser = argparse.ArgumentParser(
        prog="nlp_orchestrator.py cleanup-jobs",
        description="List (and optionally delete) old job directories under orchestrator_output/.",
    )
    parser.add_argument(
        "--output-dir", default="./orchestrator_output",
        help="Base directory to scan (default: ./orchestrator_output)",
    )
    parser.add_argument(
        "--keep-last", type=int, default=None,
        help="Keep the N most recent jobs; consider the rest for cleanup",
    )
    parser.add_argument(
        "--older-than-days", type=float, default=None,
        help="Only consider jobs older than this many days",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Actually delete the matched job directories (default: dry-run, just list them)",
    )
    args = parser.parse_args(argv)

    if args.keep_last is None and args.older_than_days is None:
        print("Specify at least one of --keep-last N or --older-than-days D.")
        return 1

    prunable = find_prunable_jobs(
        args.output_dir, keep_last=args.keep_last, older_than_days=args.older_than_days,
    )
    if not prunable:
        print("No jobs match the given criteria.")
        return 0

    print(f"{len(prunable)} job(s) match:")
    for job in prunable:
        print(f"  {job['job_id']}  ({job['timestamp']})")

    if not args.yes:
        print("\nDry run -- nothing deleted. Re-run with --yes to actually delete these.")
        return 0

    deleted, errors = delete_jobs([job["job_dir"] for job in prunable])
    print(f"\nDeleted {deleted} job director{'y' if deleted == 1 else 'ies'}.")
    for err in errors:
        print(f"  Failed: {err}")
    return 1 if errors else 0


def main():
    """Main entry point for orchestrator CLI."""
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        return _run_compare_command(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup-jobs":
        return _run_cleanup_jobs_command(sys.argv[2:])

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

  # Compare results across jobs
  python nlp_orchestrator.py compare orchestrator_output/job_A orchestrator_output/job_B

  # List old jobs older than 30 days (dry run -- add --yes to actually delete)
  python nlp_orchestrator.py cleanup-jobs --older-than-days 30
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
        default=os.getenv("LLM_PROVIDER") or NO_LLM_PROVIDER,
        help="LLM provider name: ollama, openai, anthropic, metacentrum, openai_compatible, "
             "or none (default: LLM_PROVIDER, else none)"
    )
    parser.add_argument(
        "--llm-endpoint",
        default=os.getenv("LLM_ENDPOINT", "http://localhost:11434"),
        help="LLM API endpoint URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_MODEL", ""),
        help="LLM model name to use (default: LLM_MODEL)"
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.getenv("LLM_API_KEY"),
        help="Optional API key for LLM providers that require authentication"
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("LLM_BASE_URL"),
        help="Base URL for openai_compatible servers (e.g. http://localhost:1234/v1 for LM Studio) or to override metacentrum's default gateway URL"
    )
    parser.add_argument(
        "--output-dir",
        default="./orchestrator_output",
        help="Base directory for workflow outputs (default: ./orchestrator_output)"
    )
    parser.add_argument(
        "--protein-file",
        default=None,
        help="Path to a local protein .pdb/.cif file to use instead of downloading from RCSB"
    )
    parser.add_argument(
        "--ligand-files",
        action="append",
        default=None,
        help="Path to a local ligand file (.sdf/.mol/.mol2/.pdbqt) to use instead of downloading from RCSB. "
             "Repeat the flag for multiple ligands (e.g. --ligand-files a.sdf --ligand-files b.sdf)."
    )
    parser.add_argument(
        "--ligand-folder",
        default=None,
        help="Path to a directory of local ligand files (.sdf/.mol/.mol2/.pdbqt, scanned recursively) "
             "to dock against. Overrides --ligand-files and any ligand IDs in the command."
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
    llm_client = None
    if is_llm_configured(args.llm_provider):
        llm_client = create_llm_client(
            provider=args.llm_provider,
            endpoint=args.llm_endpoint,
            model=args.llm_model,
            api_key=args.llm_api_key,
            base_url=args.llm_base_url,
        )
    orchestrator = WorkflowOrchestrator(output_base=args.output_dir, verbosity=current_verbosity)
    parser_obj = WorkflowParser()

    # Handle special commands
    if args.check_health:
        return _check_health(llm_client, args, orchestrator)

    if llm_client is None and (args.list_models or args.interactive or args.command):
        print(NO_LLM_MESSAGE)
        return 1
    if not args.llm_model and (args.interactive or args.command):
        print(NO_MODEL_MESSAGE.format(provider=args.llm_provider))
        return 1

    if args.list_models:
        print(f"Available models for provider '{args.llm_provider}':")
        models = llm_client.list_models()
        if models:
            for model in models:
                print(f"  - {model}")
        else:
            print("  No models found or provider endpoint not accessible")
        return 0
    
    # Interactive mode
    if args.interactive:
        return _run_interactive_loop(
            llm_client=llm_client,
            orchestrator=orchestrator,
            parser_obj=parser_obj,
            clarification_mode=args.clarification_mode,
            protein_file=args.protein_file,
            ligand_files=args.ligand_files,
            ligand_folder=args.ligand_folder,
        )

    # Single command mode
    if args.command:
        return process_workflow(
            args.command,
            llm_client,
            orchestrator,
            parser_obj,
            clarification_mode=args.clarification_mode,
            protein_file=args.protein_file,
            ligand_files=args.ligand_files,
            ligand_folder=args.ligand_folder,
        )
    else:
        parser.print_help()
        return 1


def _check_health(llm_client: Optional[BaseLLMClient], args: argparse.Namespace, orchestrator: WorkflowOrchestrator) -> int:
    """`--check-health`: 0 when every MCP service is healthy and the LLM, if
    one is configured, answers. No LLM is information, not a failure: the
    GUI's forms dock without one."""
    print("Checking service health...")
    print("\nLLM Provider:")
    if llm_client is None:
        llm_ok = True
        print("  ➖ not configured (only natural-language commands need one; see --llm-provider)")
    else:
        llm_ok = llm_client.check_health()
        print(f"  {'✅' if llm_ok else '❌'} {args.llm_provider} ({llm_client.endpoint})")

    print("\nMCP Services:")
    health = orchestrator.check_services_health()
    for service, ok in health.items():
        print(f"  {'✅' if ok else '❌'} {service}")

    if llm_ok and all(health.values()):
        print("\n✅ All services are healthy!")
        return 0
    print("\n❌ Some services are not healthy. Please start them first.")
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
    llm_client: BaseLLMClient,
    max_turns: int = 8,
) -> None:
    """Hybrid clarification: deterministic branching + LLM wording/intent extraction."""
    prompt_order = ["protein_id", "ligand_ids", "center", "box_size"]
    turn = 0

    while parser_obj.has_missing_parameters() and turn < max_turns:
        turn += 1
        missing = parser_obj.get_missing_parameters()
        current_param = next((p for p in prompt_order if p in missing), missing[0])

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


def _default_clarification_mode(mode: str) -> str:
    """Normalize clarification mode with hybrid as safe default."""
    if mode == CLARIFICATION_MODE_LEGACY:
        return CLARIFICATION_MODE_LEGACY
    return CLARIFICATION_MODE_HYBRID


def _collect_missing_parameters(parser_obj: WorkflowParser) -> None:
    """Collect missing or invalid required fields in short step-by-step prompts."""
    prompt_order = ["protein_id", "ligand_ids", "center", "box_size"]
    center_default = [10.0, 20.0, 30.0]
    box_size_default = [25.0, 30.0, 35.0]

    while parser_obj.has_missing_parameters():
        missing = parser_obj.get_missing_parameters()
        current_param = next((p for p in prompt_order if p in missing), missing[0])

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

        elif current_param == "docking_algorithm":
            while True:
                value = normalize_docking_algorithm(
                    input("Which docking algorithm: 'moldina' (PSO) or 'vina' (Monte Carlo)? ").strip()
                )
                if value:
                    parser_obj.update_parameters({"docking_algorithm": value})
                    break
                print("Please answer 'moldina' or 'vina'.")

        # Re-run validation so missing/invalid fields are always refreshed.
        parser_obj.validate_workflow(
            {
                "services": parser_obj.parsed_workflow["services"],
                "parameters": parser_obj.parsed_workflow["parameters"],
            }
        )


def process_workflow(
    user_input: str,
    llm_client: BaseLLMClient,
    orchestrator: WorkflowOrchestrator,
    parser_obj: WorkflowParser,
    clarification_mode: str = CLARIFICATION_MODE_HYBRID,
    session: Optional[InteractiveSession] = None,
    protein_file: Optional[str] = None,
    ligand_files: Optional[List[str]] = None,
    ligand_folder: Optional[str] = None,
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

        # Apply local file overrides from CLI flags. A local protein_file always
        # takes precedence over an LLM-parsed protein_id (only one protein per run).
        # Local ligand_files are *combined* with any LLM-parsed ligand_ids — both
        # sources are fetched/copied and docked together. ligand_folder supersedes
        # both ligand_files and ligand_ids (every file in the folder is docked).
        if protein_file or ligand_files or ligand_folder:
            parsed.setdefault("parameters", {})
            if protein_file:
                parsed["parameters"]["protein_file"] = protein_file
                print(f"📁 Using local protein file: {protein_file}")
            if ligand_folder:
                parsed["parameters"]["ligand_folder"] = ligand_folder
                print(f"📁 Using local ligand folder: {ligand_folder}")
            elif ligand_files:
                parsed["parameters"]["ligand_files"] = ligand_files
                print(f"📁 Using local ligand file(s): {', '.join(ligand_files)}")
            services = parsed.setdefault("services", [])
            if "get_input_files" not in services:
                services.append("get_input_files")

        # Validate and enrich
        print("✅ Validating workflow...")
        workflow = parser_obj.validate_workflow(parsed)

        if not _auto_resolve_named_ligands_for_workflow(orchestrator, workflow):
            return 1
        parser_obj.validate_workflow(workflow)

        # Persist workflow parameters immediately after parsing/validation.
        shared_job_id = generate_job_id()
        state_manager = WorkflowStateManager(orchestrator.output_base / shared_job_id)
        parser_obj.set_state_manager(state_manager)
        state_manager.save_parameters_checkpoint(workflow.get("parameters", {}), source="initial_parse")
        
        # Show summary
        print("\n" + parser_obj.get_workflow_summary())
        

        clarification_mode = _default_clarification_mode(clarification_mode)

        # Check for missing parameters
        if parser_obj.has_missing_parameters():
            print("\n⚠️  I need a bit more information before running this workflow.")
            print(parser_obj.format_missing_prompt())

            # Ask only for missing/invalid fields, one short question at a time.
            if clarification_mode == CLARIFICATION_MODE_HYBRID:
                _collect_missing_parameters_hybrid(parser_obj, llm_client=llm_client)
            else:
                _collect_missing_parameters(parser_obj)

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
                    # Nothing ran: the planned steps are kept apart, so a
                    # later rerun runs them all instead of only the steps
                    # its change invalidates, which have no inputs yet
                    # (Review Stage R1, Phase 15).
                    last_results={
                        "status": "cancelled",
                        "services_executed": [],
                        "services_planned": workflow.get("services", []),
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

        return 0
        
    except Exception as e:
        logger.error(f"Failed to process workflow: {e}", exc_info=True)
        print(f"\n❌ Failed to process workflow: {e}")
        return 1
    finally:
        parser_obj.set_state_manager(None)


if __name__ == "__main__":
    sys.exit(main())
