"""Streamlit front-end for the Molecular Docking Platform."""
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from common.path_utils import from_container_path
from common.pdb_geometry import compute_protein_center_and_box
from common.utils import generate_job_id
from services.llm_orchestrator.interactive_session import (
    compute_changed_params,
    resolve_prepared_protein_pdb_path,
)
from services.llm_orchestrator.job_comparator import compare_jobs
from services.llm_orchestrator.rerun_planner import (
    compute_rerun_steps,
    describe_rerun_plan,
    missing_required_params,
)
from services.llm_orchestrator.workflow_llm_parser import (
    _deterministic_intent,
    detect_protein_geometry_command,
    parse_followup_with_llm,
    parse_workflow_with_llm,
)
from services.llm_orchestrator.workflow_definition import CORE_PARAMS, get_default_parameters
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.job_history import escape_markdown, list_completed_jobs, list_failed_jobs, rename_job
from ui.mol_viewer import render_mol_viewer
from ui.ollama_models import list_ollama_models
from ui.orchestrator_bridge import OrchestratorBridge
from ui.results import build_comparison_html, make_affinity_chart_figure
from ui.session_state import (
    DETECTED_CPU_COUNT,
    apply_parsed_params,
    apply_settings,
    clear_box_picker_state,
    get_params,
    init,
    load_session_params,
    start_new_session,
)

st.set_page_config(
    layout="wide",
    page_title="Molecular Docking Platform",
    page_icon="🧬",
)

init()

OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "/workspace/orchestrator_output"))
UPLOAD_DIR = OUTPUT_BASE / "uploads"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_upload(uploaded_file, upload_dir: Path) -> str:
    """Save an uploaded file under upload_dir, using only its basename.

    Streamlit's upload endpoint is a plain HTTP POST exposing whatever
    filename the client sends; without stripping directory components a
    crafted name (e.g. "../../etc/x") could write outside upload_dir.
    """
    safe_name = Path(uploaded_file.name).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"Unusable uploaded filename: {uploaded_file.name!r}")

    dest = upload_dir / safe_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(uploaded_file.getbuffer())
    return str(dest)


def _collect_form_params() -> dict:
    """Read widget keys and return a params dict for validation/execution."""
    params: dict = {}
    mode = st.session_state.get("input_mode", "local_files")

    if mode == "rcsb":
        pid = st.session_state.get("param_protein_id", "").strip()
        if pid:
            params["protein_id"] = pid
        ligand_text = st.session_state.get("param_ligand_ids_text", "").strip()
        if ligand_text:
            params["ligand_ids"] = [l.strip() for l in ligand_text.split(",") if l.strip()]

    elif mode == "folder":
        pid = st.session_state.get("param_protein_id", "").strip()
        if pid:
            params["protein_id"] = pid
        # ligand_files already written to params via _on_folder_ligand_upload_change

    # local_files: protein_file / ligand_files already written to params during upload

    try:
        cx = float(st.session_state.get("param_center_x", ""))
        cy = float(st.session_state.get("param_center_y", ""))
        cz = float(st.session_state.get("param_center_z", ""))
        params["center"] = [cx, cy, cz]
    except (ValueError, TypeError):
        pass

    try:
        bx = float(st.session_state.get("param_box_x", ""))
        by = float(st.session_state.get("param_box_y", ""))
        bz = float(st.session_state.get("param_box_z", ""))
        params["box_size"] = [bx, by, bz]
    except (ValueError, TypeError):
        pass

    for key, default, cast in [
        ("ph", "7.4", float),
        ("exhaustiveness", "8", int),
        ("cpu", "1", int),
    ]:
        try:
            params[key] = cast(st.session_state.get(f"param_{key}", default))
        except (ValueError, TypeError):
            pass

    params["parallel"] = bool(st.session_state.get("param_parallel", False))
    try:
        params["max_workers"] = max(1, int(st.session_state.get("param_max_workers", 1)))
    except (ValueError, TypeError):
        params["max_workers"] = 1

    return params


# ── Chat handler ──────────────────────────────────────────────────────────────

def _find_receptor_pdb_for_geometry() -> Path | None:
    """Find the prepared receptor PDB to compute protein geometry from.

    Prefers the box picker's already-loaded receptor (freshest, if the user
    used that control), else falls back to the active session's last
    completed ``prepare_protein`` result -- the same two sources the box
    picker and results viewer already read from. Returns ``None`` if
    neither is available or the file is no longer on disk.
    """
    raw_path = st.session_state.get("_box_picker_receptor_pdb")
    if not raw_path:
        session = st.session_state.get("interactive_session")
        last_results = getattr(session, "last_results", None) if session else None
        raw_path = resolve_prepared_protein_pdb_path(last_results)

    if not raw_path:
        return None
    resolved = _resolve_container_path(raw_path)
    return resolved if resolved.exists() else None


def _apply_protein_geometry(receptor_path: Path, target: str) -> None:
    """Compute center/box_size from *receptor_path*'s bounding box, apply it
    to the manual Docking Box fields and the box picker's own viewer state,
    and report the result in chat.

    Shared by the immediate path (a receptor is already prepared) and the
    deferred path (``_render_box_picker()``'s bridge-completion handling,
    once a just-started fetch/prepare job finishes). Syncing the picker's
    state here too (not just the manual fields) matters for the immediate
    path specifically: no bridge transition happens there, so
    ``_render_box_picker()``'s completion handling -- the only other place
    that seeds the picker -- never runs; without this the box picker's
    viewer would keep showing a stale/no box after a geometry command even
    though the manual fields were updated correctly.
    """
    try:
        pdb_text = _read_file_bytes(
            str(receptor_path), receptor_path.stat().st_mtime
        ).decode("utf-8", errors="replace")
        center, box_size = compute_protein_center_and_box(pdb_text)
    except (OSError, ValueError) as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Could not compute protein geometry: {exc}"}
        )
        return

    if target == "center":
        apply_parsed_params({"center": center})
        message = f"Protein center set to ({center[0]}, {center[1]}, {center[2]})."
    else:
        apply_parsed_params({"box_size": box_size})
        message = (
            f"Box size set to ({box_size[0]}, {box_size[1]}, {box_size[2]}) "
            "to cover the whole protein (+10% margin)."
        )
    _sync_picker_box_from_manual_inputs()
    st.session_state.messages.append({"role": "assistant", "content": message})


def _handle_protein_geometry_command(target: str) -> None:
    """Compute center/box_size from the loaded protein's bounding box and
    apply it, in response to a "set box center as protein center" / "look
    across all protein" chat command -- see
    ``detect_protein_geometry_command()``.

    If no protein has been fetched/prepared yet but the form already has
    enough to do that (a protein source is set), this kicks off the same
    background get_input_files+prepare_protein job "Load receptor for box
    picking" uses, via ``_start_box_picker_prep()``, and stashes *target* in
    ``_pending_geometry_target`` -- ``_render_box_picker()``'s
    bridge-completion handling applies it once that job completes --
    instead of failing outright. This is what makes the geometry commands
    usable on a first message, before any button has been clicked.
    """
    receptor_path = _find_receptor_pdb_for_geometry()
    if receptor_path is not None:
        _apply_protein_geometry(receptor_path, target)
        return

    started, message, _severity = _start_box_picker_prep()
    if started:
        st.session_state["_pending_geometry_target"] = target
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": (
                    "I don't have the protein ready yet — fetching and "
                    "preparing it now, then I'll set this."
                ),
            }
        )
    else:
        st.session_state.messages.append({"role": "assistant", "content": message})


def _on_chat_submit(user_message: str) -> None:
    st.session_state.messages.append({"role": "user", "content": user_message})

    geometry_target = detect_protein_geometry_command(user_message)
    if geometry_target is not None:
        _handle_protein_geometry_command(geometry_target)
        return

    # Only the revision commands are taken from the CLI's deterministic
    # intents; everything else keeps the parameter-parsing path below.
    intent = _deterministic_intent(user_message)
    if intent is not None and intent["intent"] in ("show_history", "undo_revision"):
        _handle_history_chat_intent(intent)
        return

    with st.spinner("Parsing with LLM..."):
        try:
            prior = st.session_state.parser.parsed_workflow
            if prior and prior.get("parameters"):
                delta = parse_followup_with_llm(
                    st.session_state.llm_client,
                    user_message,
                    current_parameters=prior["parameters"],
                    current_services=prior.get("services", []),
                )
                services = list(dict.fromkeys(
                    prior.get("services", []) + delta.get("services_add", [])
                ))
                parameters = {**prior["parameters"], **delta.get("parameters", {})}
                parsed = {"services": services, "parameters": parameters}
            else:
                parsed = parse_workflow_with_llm(st.session_state.llm_client, user_message)

            validated = st.session_state.parser.validate_workflow(parsed)
            apply_parsed_params(validated["parameters"])

            p = validated["parameters"]
            missing = validated.get("missing_required", [])

            lines = ["Got it! Here's what I extracted:"]
            if p.get("protein_id"):
                lines.append(f"- Protein: **{p['protein_id']}**")
            if p.get("ligand_ids"):
                lines.append(f"- Ligands: **{', '.join(p['ligand_ids'])}**")
            if p.get("ligand_folder"):
                lines.append(f"- Ligand folder: `{p['ligand_folder']}`")
            if p.get("center"):
                c = p["center"]
                lines.append(f"- Center: ({c[0]}, {c[1]}, {c[2]})")
            if p.get("box_size"):
                b = p["box_size"]
                lines.append(f"- Box size: ({b[0]}, {b[1]}, {b[2]})")
            if missing:
                lines.append(f"\nStill needed: **{', '.join(missing)}** — please fill them in.")

            st.session_state.messages.append(
                {"role": "assistant", "content": "\n".join(lines)}
            )
        except Exception as exc:
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Sorry, I couldn't parse that: {exc}"}
            )


# ── Run / Reset ───────────────────────────────────────────────────────────────

def _handle_run_docking() -> None:
    form_params = _collect_form_params()
    st.session_state.params.update(form_params)
    params = st.session_state.params.copy()

    session = st.session_state.interactive_session
    if session.has_active_job():
        _handle_redock_request(session, params)
        return

    # A brand-new job is about to start -- any box-picker receptor loaded
    # for a previous (unrelated) protein/session no longer applies. Without
    # this, _find_receptor_pdb_for_geometry() would keep preferring that
    # stale path over this new job's own prepare_protein result once it
    # completes, silently computing geometry chat commands from the wrong
    # protein (Review Stage R1, Phase 12).
    clear_box_picker_state()

    # Always include visualization — it runs after docking and produces the PNG.
    services = ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]
    try:
        validated = st.session_state.parser.validate_workflow(
            {"services": services, "parameters": params}
        )
    except Exception as exc:
        st.error(f"Validation error: {exc}")
        return

    missing = validated.get("missing_required", [])
    if missing:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": f"Cannot start — required parameters missing: **{', '.join(missing)}**.",
            }
        )
        st.rerun()
        return

    job_id = generate_job_id()
    job_name = st.session_state.get("param_job_name", "").strip()[:60]
    st.session_state.job_id = job_id
    st.session_state.job_name = job_name
    st.session_state.viewed_job_id = job_id
    st.session_state["_job_done"] = False

    state_manager = WorkflowStateManager(OUTPUT_BASE / job_id)
    state_manager.save_parameters_checkpoint(validated["parameters"], source="initial_parse")
    state_manager.clear_draft_parameters()
    session.activate(job_id, state_manager)

    bridge = OrchestratorBridge()
    bridge.start_workflow(
        validated, job_id, str(OUTPUT_BASE), job_name=job_name, state_manager=state_manager
    )
    st.session_state.bridge = bridge

    st.session_state.messages.append(
        {"role": "assistant", "content": f"Starting docking session `{job_id}`..."}
    )
    st.rerun()


def _handle_save_session() -> None:
    """Persist the current form's parameter edits as a draft, without
    running/redocking anything. Lets edits (e.g. a box-size tweak) survive
    switching to a different session and back."""
    session = st.session_state.interactive_session
    if not session.has_active_job():
        return
    form_params = _collect_form_params()
    st.session_state.params.update(form_params)
    checkpointed = session.state_manager.load_latest_parameters() or {}
    if not compute_changed_params(checkpointed, form_params):
        st.session_state.messages.append(
            {"role": "assistant", "content": "No parameter changes to save."}
        )
    else:
        session.state_manager.save_draft_parameters(st.session_state.params)
        st.session_state.messages.append(
            {"role": "assistant", "content": "Parameters saved. Click **Run Docking** to apply them."}
        )
    st.rerun()


def _handle_redock_request(session, params: dict) -> None:
    """Diff params against the active session's last checkpoint and, if a
    rerun is warranted, stage a plan for confirmation.

    Mirrors the CLI's ``_execute_partial_rerun`` (describe_rerun_plan +
    ``[Y/n]`` prompt) via a two-step "Run Docking" → "Confirm redock" button
    pair, since Streamlit has no blocking ``input()``.
    """
    # The first run checkpoints WorkflowParser.validate_workflow()'s output,
    # which fills in defaults (energy_range, num_modes, population, ...);
    # the form doesn't carry those. Apply the same defaults here, or every
    # redock "changes" them to None -- a bogus dock/visualize plan and a
    # checkpoint that silently drops them.
    params = {**get_default_parameters(), **params}
    old_params = session.state_manager.load_latest_parameters() or {}
    changed = compute_changed_params(old_params, params)
    # pending_changed_params holds changes already checkpointed but not yet
    # run to completion: a restored revision (all CORE_PARAMS), or an
    # earlier redock that was cancelled / failed. The form matches the
    # checkpoint in those cases, so the diff alone would miss them.
    rerun_keys = changed | session.pending_changed_params
    if not rerun_keys:
        st.session_state.messages.append(
            {"role": "assistant", "content": "No parameter changes detected — nothing to redock."}
        )
        st.rerun()
        return

    if changed:
        session.state_manager.save_parameters_checkpoint(params, source="interactive_update")
        session.state_manager.clear_draft_parameters()

    original_services = (session.last_results or {}).get("services_executed", [])
    steps = compute_rerun_steps(rerun_keys, original_services=original_services)
    if not steps:
        st.session_state.messages.append(
            {"role": "assistant", "content": "Parameters updated, but no steps need to be re-executed."}
        )
        st.rerun()
        return

    # The checkpoint above now runs ahead of the results on disk until a
    # run completes (render_chat() clears this) -- Cancel or the error
    # return below must not lose that.
    session.pending_changed_params |= changed

    # params is what the checkpoint holds -- reuse it instead of a
    # redundant round trip back through the checkpoint file.
    missing = missing_required_params(steps, params)
    if missing:
        st.error("Cannot redock — required parameters are missing: " + ", ".join(sorted(set(missing))))
        return

    st.session_state["_pending_rerun"] = {
        "job_id": session.job_id,
        "steps": steps,
        "params": params,
    }
    st.rerun()


def _execute_confirmed_redock() -> None:
    pending = st.session_state.pop("_pending_rerun", None)
    if not pending:
        return

    session = st.session_state.interactive_session
    if pending.get("job_id") != session.job_id:
        # The active session changed (e.g. a sidebar Load) after this plan
        # was staged but before it was confirmed -- the staged steps/params
        # were computed for a different job_id and must not be applied here.
        st.error("Redock plan was for a different session and has been discarded — please try again.")
        st.rerun()
        return

    rerun_workflow = {"services": pending["steps"], "parameters": pending["params"]}

    st.session_state["_job_done"] = False
    st.session_state.viewed_job_id = session.job_id

    bridge = OrchestratorBridge()
    bridge.start_workflow(
        rerun_workflow,
        session.job_id,
        str(OUTPUT_BASE),
        job_name=st.session_state.get("job_name", ""),
        state_manager=session.state_manager,
    )
    st.session_state.bridge = bridge

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": f"Redocking `{session.job_id}` — steps: {', '.join(pending['steps'])}...",
        }
    )
    st.rerun()


def _undo_to_revision(revision: int) -> str:
    """Restore the active session's parameters to *revision* (the GUI's
    ``undo <rev>``) and return the reply to show.

    Shared by the Revision history panel's button (an on_click callback)
    and the chat command; never calls st.rerun() and must run before the
    parameter widgets render, since it rewrites their keys.
    """
    session = st.session_state.interactive_session
    if not session.has_active_job():
        return "No active session — run or load one first."
    if _active_bridge_job_id() is not None:
        # The orchestrator re-reads the latest parameters before every
        # step, so a restore now would change the running job mid-flight.
        return "A session is running — Stop it before restoring a revision."

    try:
        data = session.state_manager.restore_revision(revision)
    except ValueError as exc:
        return f"Cannot restore revision {revision}: {exc}"

    restored = data["parameters"]
    if restored is None:
        return (
            f"Revision {revision} has no parameter checkpoint — parameters unchanged "
            "(runtime state restored)."
        )

    # A draft would otherwise win over the restored revision on the next Load.
    session.state_manager.clear_draft_parameters()
    st.session_state.pop("_pending_rerun", None)
    _load_params_into_form(restored)
    # The results on disk come from the newer parameters -- the next redock
    # must rerun everything those affect, even though the form now matches
    # the checkpoint exactly (mirrors the CLI's undo).
    session.pending_changed_params |= CORE_PARAMS
    return (
        f"Restored revision {revision}. The results panel still shows the last run — "
        "click **Run Docking** to rerun with the restored parameters."
    )


def _format_history_markdown(state_manager: WorkflowStateManager) -> str:
    """The active job's revision history as a markdown table (the GUI's
    equivalent of the CLI's ``show history``)."""
    revisions = state_manager.list_revisions()
    if not revisions:
        return "No revisions recorded yet."

    lines = ["| Rev | Time (UTC) | Event | Details |", "|---|---|---|---|"]
    for r in revisions:
        details = ", ".join(f"{k}={v}" for k, v in r["details"].items())
        flags = [name for name, present in (("params", r["has_parameters"]), ("state", r["has_state"])) if present]
        if flags:
            details = f"{details} [{', '.join(flags)}]" if details else f"[{', '.join(flags)}]"
        timestamp = r["timestamp"][:19].replace("T", " ")
        event, details = (escape_markdown(s).replace("|", "\\|") for s in (r["event_type"], details))
        lines.append(f"| {r['revision']} | {timestamp} | {event} | {details} |")
    return "\n".join(lines)


def _handle_history_chat_intent(intent: dict) -> None:
    """Answer a deterministic ``show history`` / ``undo [N]`` chat command."""
    session = st.session_state.interactive_session
    if intent["intent"] == "show_history":
        if not session.has_active_job():
            reply = "No active session — no history yet."
        else:
            current = session.state_manager.get_parameters_revision()
            reply = (
                _format_history_markdown(session.state_manager)
                + f"\n\nParameters are currently at revision **{current}**. "
                "Type `undo <revision>` to restore one."
            )
    elif intent.get("name") is None:
        reply = "Which revision? e.g. `undo 3` — type `show history` to list them."
    else:
        reply = _undo_to_revision(int(intent["name"]))
    st.session_state.messages.append({"role": "assistant", "content": reply})


def _reset_session(preserve: frozenset = frozenset({"settings"})) -> None:
    for key in list(st.session_state.keys()):
        if key not in preserve:
            del st.session_state[key]
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────

_PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "metacentrum": "MetaCentrum (e-INFRA CZ)",
    "openai_compatible": "Other local server",
}
_PROVIDER_KEYS = {label: key for key, label in _PROVIDER_LABELS.items()}


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Settings")
        _render_settings_form()

        st.divider()
        st.subheader("Session History")
        _render_job_history()

        st.divider()
        st.subheader("Failed Sessions")
        _render_failed_session_history()


@st.cache_data(ttl=30, show_spinner="Checking available Ollama models...")
def _cached_ollama_models(endpoint: str) -> list:
    # show_spinner is a message, not False, on purpose: this runs on every
    # rerun once the 30s cache lapses, including during the active-job 2s
    # poll loop at the bottom of this file -- with no spinner the whole
    # page silently froze for up to list_ollama_models()'s timeout with no
    # indication anything was happening.
    return list_ollama_models(endpoint)


@st.cache_data(ttl=15, show_spinner=False)
def _cached_completed_jobs(output_base: str) -> list:
    # list_completed_jobs() does a full glob + JSON-parse of every job
    # directory. The sidebar re-renders this on every Streamlit rerun,
    # including the "while a job is active" 2s poll loop at the bottom of
    # this file -- without caching, that's a full orchestrator_output/ scan
    # every ~2 seconds for the entire duration of any running job, growing
    # linearly with total historical job count.
    return list_completed_jobs(output_base)


@st.cache_data(ttl=15, show_spinner=False)
def _cached_failed_jobs(output_base: str) -> list:
    # Mirrors _cached_completed_jobs's caching rationale above.
    return list_failed_jobs(output_base)


def _render_settings_form() -> None:
    settings = st.session_state.settings
    labels = list(_PROVIDER_LABELS.values())
    current_label = _PROVIDER_LABELS.get(settings.get("provider", "ollama"), labels[0])

    provider_label = st.selectbox("LLM Provider", labels, index=labels.index(current_label))
    provider = _PROVIDER_KEYS[provider_label]

    endpoint = settings.get("endpoint", "")
    # Only openai_compatible has a base_url input below; for every other
    # provider (including metacentrum, which has no override field yet)
    # drop a stale value left over from a prior openai_compatible session
    # instead of silently carrying it into the new client.
    base_url = settings.get("base_url", "") if provider == "openai_compatible" else ""
    api_key = settings.get("api_key", "")
    current_model = settings.get("model", "")

    if provider == "ollama":
        with st.expander("Advanced"):
            endpoint = st.text_input(
                "Endpoint URL",
                value=endpoint or "http://localhost:11434",
                help="Point at a different Ollama server (e.g. a GPU workstation) "
                     "instead of the bundled one.",
            )
    elif provider == "openai_compatible":
        endpoint = st.text_input(
            "Endpoint URL", value=endpoint or "http://localhost:11434"
        )
        base_url = st.text_input("Base URL (OpenAI-compatible)", value=base_url or endpoint)

    if provider == "ollama":
        available_models = _cached_ollama_models(endpoint)
        if available_models:
            options = available_models if current_model in available_models else (
                ([current_model] if current_model else []) + available_models
            )
            model = st.selectbox("Model", options, index=options.index(current_model) if current_model in options else 0)
        else:
            model = st.text_input("Model", value=current_model)
            st.caption("Could not reach Ollama to list models — showing free-text input.")
    else:
        model = st.text_input("Model", value=current_model)

    if provider in ("openai", "anthropic", "metacentrum", "openai_compatible"):
        api_key = st.text_input("API Key", value=api_key, type="password")

    if st.button("Apply settings"):
        new_settings = {
            "provider": provider,
            "model": model.strip(),
            "endpoint": endpoint.strip(),
            "api_key": api_key.strip(),
            "base_url": base_url.strip(),
        }
        try:
            apply_settings(new_settings)
            st.success("Settings applied.")
        except Exception as exc:
            st.error(f"Could not apply settings: {exc}")


def _load_params_into_form(params: dict) -> None:
    """Make *params* the form's full parameter state: params dict + every
    param_* widget (load_session_params), the box picker's own state, and
    the chat parser. Shared by a sidebar Load and a revision restore. Must
    run before the parameter widgets render (sidebar/chat code, or an
    on_click callback).
    """
    load_session_params(params)
    _sync_picker_box_from_manual_inputs()
    # Seed parser.parsed_workflow so a chat follow-up sent right after this
    # merges onto these parameters instead of being treated as a brand-new
    # workflow (see parse_followup_with_llm usage in _on_chat_submit).
    st.session_state.parser.validate_workflow(
        {
            "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
            "parameters": params,
        }
    )


def _resume_session(job: dict) -> None:
    """Load a session's results into the results panel *and* resume it as
    the active interactive session, so redocking can continue immediately
    instead of starting a brand-new job on the next "Run Docking" click.

    Reconstructing a WorkflowStateManager against an existing job_dir is
    safe -- it only creates the manifest file if missing, never overwrites
    existing checkpoints (see workflow_state_manager.py).
    """
    st.session_state.viewed_job_id = job["job_id"]
    state_manager = WorkflowStateManager(Path(job["job_dir"]))
    # Populate the "Current parameters" panel (and its param_* widgets) from
    # the job's saved parameters -- without this, the panel stays empty
    # after a Load since nothing else in this function touches
    # st.session_state.params. A draft (saved via "Save Session" without
    # docking) takes priority over the last real checkpoint since it's
    # strictly newer.
    loaded_params = state_manager.load_draft_parameters() or state_manager.load_latest_parameters()
    if loaded_params:
        _load_params_into_form(loaded_params)
    last_results = _load_workflow_results(job["job_id"])
    st.session_state.interactive_session.activate(
        job["job_id"], state_manager, last_results=last_results
    )
    st.session_state.job_id = job["job_id"]
    st.session_state.job_name = job.get("job_name", "")
    # Prevents render_chat()'s completion-sync block from immediately
    # overwriting session.last_results on the next rerun (that block only
    # runs while _job_done is False, i.e. for a run it's actively tracking).
    st.session_state["_job_done"] = True
    # A redock staged for the *previous* active session is no longer valid
    # once the active session switches to a different job_id -- discard it
    # rather than risk it being confirmed against this new job later
    # (_execute_confirmed_redock also checks job_id as a second guard).
    st.session_state.pop("_pending_rerun", None)
    st.rerun()


def _clear_all_job_caches() -> None:
    _cached_completed_jobs.clear()
    _cached_failed_jobs.clear()


def _active_bridge_job_id() -> str | None:
    """job_id this browser tab's own bridge is actively running, if any.

    Only the current tab's own bridge is visible here -- a different
    tab/process running the same job_id is not detectable, and that
    residual gap is documented rather than silently assumed solved.

    Used to guard every action that would detach ``st.session_state`` from
    the job a live bridge is writing into (New Session, Reset, Load, Delete)
    -- detaching without checking this first would orphan the running
    background thread (it keeps writing to its job_dir with nothing left in
    session_state able to observe or stop it) and, for Load/Delete
    specifically, desyncs ``job_id`` from the bridge's real target, which
    defeats the very running-job guard those actions rely on (see
    docs/reviews/stage_11_2_11_3_r2.md).
    """
    bridge = st.session_state.get("bridge")
    if bridge is not None and bridge.is_running():
        return st.session_state.get("job_id")
    return None


def _render_session_row(job: dict, detail: str, *, show_checkbox: bool) -> str | None:
    """Render one session's Load/Rename/Delete controls -- shared by Session
    History and Failed Sessions. Returns job_dir if its checkbox was
    checked (feeds "Compare selected sessions"), else None.
    """
    job_name = job.get("job_name") or ""
    if job_name:
        label = f"**{escape_markdown(job_name)}**  `{job['job_id']}`  {detail}"
    else:
        label = f"{job['job_id']}  {detail}"

    selected_dir = None
    col_first, col_load, col_rename, col_delete = st.columns([3, 1, 1, 1])
    with col_first:
        if show_checkbox:
            if st.checkbox(label, key=f"job_sel_{job['job_id']}"):
                selected_dir = job["job_dir"]
        else:
            st.markdown(label)
    active_job_id = _active_bridge_job_id()
    running_here = job["job_id"] == active_job_id
    # Loading a *different* session while this tab's bridge is running would
    # reassign st.session_state.job_id/interactive_session out from under
    # the running bridge, orphaning it and desyncing running_here for the
    # job it's actually running -- so Load is blocked for every row (not
    # just this one) while any bridge is active in this tab.
    with col_load:
        if st.button(
            "Load",
            key=f"job_load_{job['job_id']}",
            disabled=active_job_id is not None,
            help="Stop the running session before loading another." if active_job_id is not None else None,
        ):
            _resume_session(job)
    with col_rename:
        if st.button("Rename", key=f"job_rename_toggle_{job['job_id']}"):
            flag = f"_renaming_{job['job_id']}"
            st.session_state[flag] = not st.session_state.get(flag, False)

    with col_delete:
        if st.button("Delete", key=f"job_delete_toggle_{job['job_id']}", disabled=running_here):
            flag = f"_deleting_{job['job_id']}"
            st.session_state[flag] = not st.session_state.get(flag, False)
    if running_here:
        st.caption("Running in this browser tab — use Stop (in the results panel) before deleting.")

    if st.session_state.get(f"_renaming_{job['job_id']}"):
        new_name = st.text_input(
            "New name",
            value=job_name,
            key=f"job_rename_input_{job['job_id']}",
            max_chars=60,
            label_visibility="collapsed",
        )
        col_save, col_cancel = st.columns(2)
        with col_save:
            if st.button("Save", key=f"job_rename_save_{job['job_id']}"):
                try:
                    rename_job(job["job_dir"], new_name)
                except Exception as exc:
                    st.error(f"Rename failed: {exc}")
                else:
                    _clear_all_job_caches()
                    st.session_state[f"_renaming_{job['job_id']}"] = False
                    if st.session_state.get("viewed_job_id") == job["job_id"]:
                        st.session_state.job_name = new_name.strip()[:60]
                    st.rerun()
        with col_cancel:
            if st.button("Cancel", key=f"job_rename_cancel_{job['job_id']}"):
                st.session_state[f"_renaming_{job['job_id']}"] = False
                st.rerun()

    if st.session_state.get(f"_deleting_{job['job_id']}"):
        st.warning(f"Delete session `{job['job_id']}` and all its files? This cannot be undone.")
        col_confirm, col_cancel2 = st.columns(2)
        with col_confirm:
            if st.button("Confirm delete", key=f"job_delete_confirm_{job['job_id']}"):
                try:
                    shutil.rmtree(job["job_dir"])
                except OSError as exc:
                    st.error(f"Delete failed: {exc}")
                else:
                    _clear_all_job_caches()
                    if st.session_state.get("viewed_job_id") == job["job_id"]:
                        st.session_state.viewed_job_id = None
                    if st.session_state.get("job_id") == job["job_id"]:
                        start_new_session()
                    st.session_state[f"_deleting_{job['job_id']}"] = False
                    st.rerun()
        with col_cancel2:
            if st.button("Cancel", key=f"job_delete_cancel_{job['job_id']}"):
                st.session_state[f"_deleting_{job['job_id']}"] = False
                st.rerun()

    return selected_dir


def _render_job_history() -> None:
    jobs = _cached_completed_jobs(str(OUTPUT_BASE))

    if not jobs:
        st.caption("No completed sessions yet.")
        return

    selected = []
    for job in jobs[:10]:
        detail = f"{job['protein_id']} ({job['ligand_count']} ligand(s))"
        job_dir = _render_session_row(job, detail, show_checkbox=True)
        if job_dir:
            selected.append(job_dir)

    if len(selected) >= 2 and st.button("Compare selected sessions"):
        try:
            st.session_state.comparison = compare_jobs(selected)
        except Exception as exc:
            st.error(f"Comparison failed: {exc}")


def _render_failed_session_history() -> None:
    jobs = _cached_failed_jobs(str(OUTPUT_BASE))

    if not jobs:
        st.caption("No failed or cancelled sessions.")
        return

    for job in jobs[:10]:
        first_error = job.get("first_error", "Unknown error")
        if len(first_error) > 80:
            first_error = first_error[:77] + "..."
        detail = f"{job['protein_id']} — {first_error}"
        _render_session_row(job, detail, show_checkbox=False)


# ── Chat panel ────────────────────────────────────────────────────────────────

def render_chat() -> None:
    job_id = st.session_state.get("job_id")
    if job_id:
        st.caption(f"Session: {_job_label(job_id, st.session_state.get('job_name'))}")
    st.subheader("Chat")

    # Stream progress lines from a running job into the last assistant message.
    bridge: OrchestratorBridge = st.session_state.bridge
    if bridge and bridge.is_running():
        new_lines = bridge.get_log_lines()
        if new_lines and st.session_state.messages:
            st.session_state.messages[-1]["content"] += "\n" + "\n".join(new_lines)

    with st.container(height=1000):
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Handle job completion between polls.
    if bridge and not bridge.is_running():
        result = bridge.get_result()
        error = bridge.get_error()
        if not st.session_state.get("_job_done"):
            # Sync the session's view of "what's been run" from the
            # already-merged result execute_workflow() returns, so a
            # subsequent redock's original_services/last_results reflect
            # this run instead of going stale -- reusing the on-disk merge
            # execute_workflow() already does rather than re-deriving it here
            # (see docs/reviews/stage_11_1_r1.md finding #5).
            session = st.session_state.interactive_session
            if result is not None and session.has_active_job() and session.job_id == st.session_state.job_id:
                session.last_results = result

            if result and result.get("status") == "completed":
                st.session_state["_job_done"] = True
                # The checkpointed parameters have now actually been run.
                # Only a *completed* run clears them (stricter than the
                # CLI): after a stop/failure the results on disk still
                # predate them, so the next redock must still include them.
                if session.job_id == st.session_state.job_id:
                    session.pending_changed_params = set()
                st.session_state.messages.append(
                    {"role": "assistant", "content": "Workflow completed successfully!"}
                )
            elif error:
                st.session_state["_job_done"] = True
                st.session_state.messages.append(
                    {"role": "assistant", "content": f"Workflow failed: {error}"}
                )
            elif result and result.get("status") == "cancelled":
                st.session_state["_job_done"] = True
                st.session_state.messages.append(
                    {"role": "assistant", "content": "Session stopped."}
                )
            elif result and result.get("status") == "failed":
                st.session_state["_job_done"] = True
                failure_detail = "; ".join(result.get("errors", [])) or "unknown error"
                st.session_state.messages.append(
                    {"role": "assistant", "content": f"Workflow failed: {failure_detail}"}
                )
            # The messages above were appended after this run already drew
            # the chat history, and with the bridge finished the
            # bottom-of-page poll no longer reruns -- rerun once so the reply
            # actually shows (can't loop: _job_done is now set).
            if st.session_state.get("_job_done"):
                st.rerun()

    if prompt := st.chat_input("Describe your workflow..."):
        _on_chat_submit(prompt)
        st.rerun()


# ── Parameters panel ──────────────────────────────────────────────────────────

# Smallest box edge (Å) the box picker's size inputs accept.
_PICKER_MIN_BOX_SIZE = 1.0

_BOX_WIDGET_KEYS = (
    "param_center_x", "param_center_y", "param_center_z",
    "param_box_x", "param_box_y", "param_box_z",
)


def _render_params_summary() -> None:
    params = st.session_state.params
    box_fields_set = any(str(st.session_state.get(k, "")).strip() for k in _BOX_WIDGET_KEYS)
    if not params and not box_fields_set:
        return

    # Protein
    if params.get("protein_id"):
        protein = params["protein_id"]
    elif params.get("protein_file"):
        protein = Path(params["protein_file"]).name
    else:
        protein = "—"

    # Ligands
    if params.get("ligand_ids"):
        ligands = ", ".join(params["ligand_ids"])
    elif params.get("ligand_files"):
        ligands = ", ".join(Path(p).name for p in params["ligand_files"])
    else:
        ligands = "—"

    # Docking box — read from the Docking Box fields, not params["center"] /
    # params["box_size"]: the fields are what _collect_form_params() actually
    # docks with, and every writer updates them (manual entry, box-picker
    # click/resize, chat-parsed values, geometry commands), whereas the
    # params lists only change on chat parsing or a run -- preferring them
    # left the summary stale after a visual pick or resize.
    def _coord(widget_keys: tuple) -> str:
        parts = [str(st.session_state.get(k, "")).strip() for k in widget_keys]
        return " / ".join(p if p else "—" for p in parts)

    center = _coord(_BOX_WIDGET_KEYS[:3])
    box = _coord(_BOX_WIDGET_KEYS[3:])

    with st.expander("Current parameters", expanded=True):
        st.markdown(
            f"**Protein:** {protein}  \n"
            f"**Ligands:** {ligands}  \n"
            f"**Center:** {center}  \n"
            f"**Box:** {box}"
        )


def _on_restore_revision_click() -> None:
    # A callback, not an inline `if st.button(...)`: restoring rewrites the
    # param_*/picker_box_* widget keys, which is only allowed before those
    # widgets render -- inline, this panel's widgets already exist.
    revision = st.session_state.get("revision_to_restore")
    if revision is None:
        return
    st.session_state.messages.append(
        {"role": "assistant", "content": _undo_to_revision(int(revision))}
    )


def _render_revision_history() -> None:
    session = st.session_state.interactive_session
    if not session.has_active_job():
        return
    state_manager = session.state_manager

    with st.expander("Revision history"):
        st.markdown(_format_history_markdown(state_manager))
        current = state_manager.get_parameters_revision()
        if current is not None:
            st.caption(f"Parameters are currently at revision {current}.")

        param_revisions = [
            r["revision"] for r in reversed(state_manager.list_revisions()) if r["has_parameters"]
        ]
        if not param_revisions:
            return
        running = _active_bridge_job_id() is not None
        # A selection left over from another session's history (after a Load).
        if st.session_state.get("revision_to_restore") not in param_revisions:
            st.session_state.pop("revision_to_restore", None)
        st.selectbox("Revision", param_revisions, key="revision_to_restore")
        st.button(
            "Restore revision",
            on_click=_on_restore_revision_click,
            disabled=running,
            use_container_width=True,
            help=(
                "A session is running — Stop it before restoring a revision."
                if running
                else "Restore that revision's parameters (recorded as a new revision); "
                     "Run Docking then reruns with them."
            ),
        )


def render_parameters_panel() -> None:
    st.subheader("Input & Parameters")

    # Shown at the top but filled in last: the box picker below can still
    # change the Docking Box fields during this run (e.g. mirroring a box
    # resize), and the summary must reflect that, not the pre-run values.
    summary_slot = st.container()

    mode = st.radio(
        "Input mode",
        options=["rcsb", "local_files", "folder"],
        format_func={"rcsb": "RCSB IDs", "local_files": "Local Files", "folder": "Batch Processing"}.get,
        horizontal=True,
        key="input_mode",
    )

    st.divider()

    if mode == "rcsb":
        _render_rcsb_inputs()
    elif mode == "local_files":
        _render_local_file_inputs()
    else:
        _render_folder_inputs()

    st.divider()
    _render_box_picker()

    st.divider()
    _render_docking_box()

    with summary_slot:
        _render_params_summary()
        _render_revision_history()

    with st.expander("Advanced"):
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("pH", key="param_ph")
        with col2:
            st.text_input("Exhaustiveness", key="param_exhaustiveness")

        st.checkbox("Parallel ligand docking", key="param_parallel")
        st.number_input(
            "Max parallel workers",
            key="param_max_workers",
            min_value=1,
            max_value=DETECTED_CPU_COUNT,
            disabled=not st.session_state.get("param_parallel", False),
        )
        st.caption(
            f"{DETECTED_CPU_COUNT} CPU core(s) detected on this machine. Running many "
            "ligands in parallel increases memory usage — lower this if docking sessions "
            "start failing unexpectedly."
        )

    st.divider()
    st.text_input(
        "Session name (optional)",
        key="param_job_name",
        max_chars=60,
        help="A label to help you find this session later in the history panel.",
    )

    st.divider()
    pending_rerun = st.session_state.get("_pending_rerun")
    if pending_rerun:
        st.info(describe_rerun_plan(pending_rerun["steps"]))
        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button("Confirm redock", type="primary", use_container_width=True):
                _execute_confirmed_redock()
        with col_cancel:
            if st.button("Cancel", use_container_width=True):
                st.session_state.pop("_pending_rerun", None)
                st.rerun()
    else:
        # New Session/Reset both detach st.session_state from whatever job
        # is currently attached -- while this tab's own bridge is actively
        # running that job, detaching would orphan the background thread
        # (nothing left in session_state could observe or stop it) and
        # desync the sidebar's running-job guards for it (see
        # _active_bridge_job_id docstring).
        running_job = _active_bridge_job_id()
        col_run, col_save, col_new, col_reset = st.columns(4)
        with col_run:
            if st.button("Run Docking", type="primary", use_container_width=True):
                _handle_run_docking()
        with col_save:
            if st.button(
                "Save Session",
                use_container_width=True,
                disabled=not st.session_state.interactive_session.has_active_job(),
                help="Save current parameters without docking.",
            ):
                _handle_save_session()
        with col_new:
            if st.button(
                "New Session",
                use_container_width=True,
                disabled=not st.session_state.interactive_session.has_active_job() or running_job is not None,
                help=(
                    "A session is running — Stop it (in the results panel) before starting a new one."
                    if running_job is not None
                    else "Keep your parameters, start a fresh session."
                ),
            ):
                start_new_session()
                st.rerun()
        with col_reset:
            if st.button(
                "Reset",
                use_container_width=True,
                disabled=running_job is not None,
                help=(
                    "A session is running — Stop it (in the results panel) before resetting."
                    if running_job is not None
                    else "Clear everything, including entered parameters."
                ),
            ):
                _reset_session()


def _on_protein_id_change() -> None:
    pid = st.session_state.get("param_protein_id", "").strip()
    old = st.session_state.params.get("protein_id", "")
    if pid and pid != old:
        st.session_state.params["protein_id"] = pid
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Updated protein ID: **{pid}**"}
        )


def _on_ligand_ids_change() -> None:
    text = st.session_state.get("param_ligand_ids_text", "").strip()
    ids = [l.strip() for l in text.split(",") if l.strip()]
    if ids and ids != st.session_state.params.get("ligand_ids"):
        st.session_state.params["ligand_ids"] = ids
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Updated ligands: **{', '.join(ids)}**"}
        )


def _render_rcsb_inputs() -> None:
    st.text_input(
        "Protein ID (PDB)",
        placeholder="e.g. 6lu7",
        key="param_protein_id",
        on_change=_on_protein_id_change,
    )
    st.text_input(
        "Ligand IDs (comma-separated)",
        placeholder="e.g. ATP, HEM, ADP",
        key="param_ligand_ids_text",
        on_change=_on_ligand_ids_change,
    )


def _on_protein_upload_change() -> None:
    uploaded = st.session_state.get("upload_protein")
    if uploaded is not None:
        path = _save_upload(uploaded, UPLOAD_DIR)
        st.session_state.params["protein_file"] = str(path)
        st.session_state.params.pop("protein_id", None)
        if st.session_state.get("_last_protein_file") != uploaded.name:
            st.session_state["_last_protein_file"] = uploaded.name
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Local protein file set: **{uploaded.name}**"}
            )
    else:
        st.session_state.params.pop("protein_file", None)
        st.session_state.pop("_last_protein_file", None)


def _on_ligand_upload_change() -> None:
    uploaded_list = st.session_state.get("upload_ligands") or []
    names = tuple(f.name for f in uploaded_list)
    if names:
        paths = [str(_save_upload(f, UPLOAD_DIR)) for f in uploaded_list]
        st.session_state.params["ligand_files"] = paths
        st.session_state.params.pop("ligand_ids", None)
        if names != tuple(st.session_state.get("_last_ligand_files", ())):
            st.session_state["_last_ligand_files"] = names
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": f"{len(uploaded_list)} local ligand file(s) set: **{', '.join(names)}**",
                }
            )
    else:
        st.session_state.params.pop("ligand_files", None)
        st.session_state.pop("_last_ligand_files", None)


def _render_local_file_inputs() -> None:
    st.file_uploader(
        "Protein file (.pdb, .cif) — drag and drop or click to browse",
        type=["pdb", "cif"],
        accept_multiple_files=False,
        key="upload_protein",
        on_change=_on_protein_upload_change,
    )
    if st.session_state.params.get("protein_file"):
        st.caption(f"Protein: {Path(st.session_state.params['protein_file']).name}")

    st.file_uploader(
        "Ligand file(s) (.sdf, .mol, .mol2, .pdbqt) — drag and drop or click to browse. "
        "A multi-molecule .sdf is split into one ligand per molecule.",
        type=["sdf", "mol", "mol2", "pdbqt"],
        accept_multiple_files=True,
        key="upload_ligands",
        on_change=_on_ligand_upload_change,
    )
    if st.session_state.params.get("ligand_files"):
        st.caption(f"{len(st.session_state.params['ligand_files'])} ligand file(s) ready")


def _on_folder_ligand_upload_change() -> None:
    uploaded_list = st.session_state.get("upload_folder_ligands") or []
    names = tuple(f.name for f in uploaded_list)
    if names:
        paths = [str(_save_upload(f, UPLOAD_DIR)) for f in uploaded_list]
        st.session_state.params["ligand_files"] = paths
        st.session_state.params.pop("ligand_ids", None)
        st.session_state.params.pop("ligand_folder", None)
        if names != tuple(st.session_state.get("_last_folder_ligand_files", ())):
            st.session_state["_last_folder_ligand_files"] = names
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": f"{len(uploaded_list)} local ligand file(s) set: **{', '.join(names)}**",
                }
            )
    else:
        st.session_state.params.pop("ligand_files", None)
        st.session_state.pop("_last_folder_ligand_files", None)


def _render_folder_inputs() -> None:
    st.text_input(
        "Protein ID (PDB)",
        placeholder="e.g. 6lu7",
        key="param_protein_id",
        on_change=_on_protein_id_change,
    )
    st.file_uploader(
        "Ligand file(s) (.sdf, .mol, .mol2, .pdbqt) — drag and drop or click to browse. "
        "A multi-molecule .sdf is split into one ligand per molecule.",
        type=["sdf", "mol", "mol2", "pdbqt"],
        accept_multiple_files=True,
        key="upload_folder_ligands",
        on_change=_on_folder_ligand_upload_change,
    )
    if st.session_state.params.get("ligand_files"):
        st.caption(f"{len(st.session_state.params['ligand_files'])} ligand file(s) ready")


def _mirror_box_to_manual_inputs(center: list, box_size: list) -> None:
    """Write picked/adjusted values into the manual text-input keys, which
    _collect_form_params() reads -- the picker augments the manual form
    rather than bypassing it, so "Run Docking" needs no separate code path.
    Must run before _render_docking_box() instantiates those widgets.
    """
    st.session_state["param_center_x"] = f"{center[0]:.3f}"
    st.session_state["param_center_y"] = f"{center[1]:.3f}"
    st.session_state["param_center_z"] = f"{center[2]:.3f}"
    st.session_state["param_box_x"] = f"{box_size[0]:.3f}"
    st.session_state["param_box_y"] = f"{box_size[1]:.3f}"
    st.session_state["param_box_z"] = f"{box_size[2]:.3f}"


def _sync_picker_box_from_manual_inputs() -> None:
    """Unconditionally mirror the manual Docking Box fields into the box
    picker's own center/size state (``picked_center`` / ``picker_box_x/y/z``
    / ``_box_picker_last_size``).

    The picker keeps this as separate state (rather than reading the manual
    fields directly) because it's also written the other way -- clicking an
    atom in the 3D viewer sets ``picked_center`` first and mirrors it out to
    the manual fields (``_mirror_box_to_manual_inputs``). Anything that
    changes the manual fields *without* going through the picker (a chat
    geometry command, applying LLM-parsed parameters) has to push the
    update here explicitly too, or the viewer silently keeps showing the
    old box -- see ``_apply_protein_geometry``, which calls this after
    updating either field so the viewer reflects a geometry command
    immediately regardless of whether the receptor it read was already
    loaded or was just fetched for that command.
    """
    try:
        center = [
            float(st.session_state.get("param_center_x", "")),
            float(st.session_state.get("param_center_y", "")),
            float(st.session_state.get("param_center_z", "")),
        ]
        st.session_state["picked_center"] = center
    except (ValueError, TypeError):
        pass

    try:
        box_size = [
            float(st.session_state.get("param_box_x", "")),
            float(st.session_state.get("param_box_y", "")),
            float(st.session_state.get("param_box_z", "")),
        ]
    except (ValueError, TypeError):
        return
    # The picker's number_inputs enforce min_value; pushing a smaller value
    # (e.g. a half-typed "0.5") into their keys would raise. Leave the picker
    # as-is until the fields hold a size it can display.
    if min(box_size) >= _PICKER_MIN_BOX_SIZE:
        st.session_state["picker_box_x"] = box_size[0]
        st.session_state["picker_box_y"] = box_size[1]
        st.session_state["picker_box_z"] = box_size[2]
        st.session_state["_box_picker_last_size"] = box_size


def _seed_picker_box_from_manual_inputs() -> None:
    """If the manual Docking Box fields already hold a valid center/size
    (from a previous pick, manual entry, or a chat geometry command) and
    the picker has no center yet, seed the picker's widget/session state
    from them so the box is visible immediately on load instead of only
    after a click.

    Guarded (unlike ``_sync_picker_box_from_manual_inputs``) because this
    runs on every receptor load and must not clobber a center the user
    already picked by clicking in the 3D viewer.
    """
    if st.session_state.get("picked_center") is not None:
        return
    _sync_picker_box_from_manual_inputs()


def _start_box_picker_prep() -> tuple[bool, str | None, str]:
    """Validate params and start the background get_input_files +
    prepare_protein job that fetches/prepares a receptor for box picking.

    Returns ``(started, message, severity)``. On success ``started`` is
    True and ``message`` is None. On failure ``started`` is False and
    ``message``/``severity`` ("error" or "warning") describe why, for the
    caller to surface however fits its UI (an inline st.error/st.warning
    for the button, a chat reply for the geometry auto-fetch below).
    """
    form_params = _collect_form_params()
    st.session_state.params.update(form_params)
    params = st.session_state.params.copy()

    prep_services = ["get_input_files", "prepare_protein"]
    try:
        validated = st.session_state.parser.validate_workflow(
            {"services": prep_services, "parameters": params}
        )
    except Exception as exc:
        return False, f"Validation error: {exc}", "error"

    missing = validated.get("missing_required", [])
    if missing:
        return False, f"Provide a protein source first — missing: {', '.join(missing)}", "warning"

    job_id = generate_job_id()
    st.session_state["_box_picker_job_id"] = job_id
    clear_box_picker_state()

    bridge = OrchestratorBridge()
    bridge.start_workflow(validated, job_id, str(OUTPUT_BASE), services=prep_services)
    st.session_state.box_picker_bridge = bridge
    return True, None, ""


def _handle_pick_box() -> None:
    """Button handler for "Load receptor for box picking"."""
    started, message, severity = _start_box_picker_prep()
    if started:
        st.rerun()
    elif severity == "error":
        st.error(message)
    else:
        st.warning(message)


def _render_box_picker() -> None:
    st.markdown("**Pick Docking Box Visually**")

    bridge: OrchestratorBridge | None = st.session_state.get("box_picker_bridge")

    if bridge and bridge.is_running():
        st.info("Preparing receptor for box picking...")
        return

    if bridge and not bridge.is_running():
        error = bridge.get_error()
        result = bridge.get_result()
        # A geometry chat command ("use protein center...") may have started
        # this very job (no receptor was loaded yet) -- pop its target now
        # so it's resolved (or reported as failed) exactly once, however
        # this job turns out.
        pending_target = st.session_state.pop("_pending_geometry_target", None)
        if error:
            st.error(f"Could not prepare receptor: {error}")
            if pending_target:
                st.session_state.messages.append(
                    {"role": "assistant", "content": f"Could not fetch the protein: {error}"}
                )
        elif result and result.get("status") == "completed":
            pdb_path = resolve_prepared_protein_pdb_path(result)
            if pdb_path:
                st.session_state["_box_picker_receptor_pdb"] = pdb_path
                if pending_target:
                    _apply_protein_geometry(_resolve_container_path(pdb_path), pending_target)
                _seed_picker_box_from_manual_inputs()
            else:
                st.error("Receptor was prepared but no PDB path was returned.")
                if pending_target:
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": "Receptor was prepared but no PDB path was returned -- could not set this.",
                        }
                    )
        elif result and result.get("status") == "failed":
            failure_detail = "; ".join(result.get("errors", [])) or "unknown error"
            st.error("Could not prepare receptor: " + failure_detail)
            if pending_target:
                st.session_state.messages.append(
                    {"role": "assistant", "content": f"Could not fetch the protein: {failure_detail}"}
                )
        st.session_state.box_picker_bridge = None
        # The chat column already rendered this run, and with the bridge
        # cleared the bottom-of-page polling rerun won't fire -- rerun once so
        # the reply appended above actually shows up (this also lets the
        # picker_box_* values written by _apply_protein_geometry settle
        # before their number_inputs render, avoiding Streamlit's
        # default-value-vs-Session-State warning).
        if pending_target:
            st.rerun()

    receptor_raw = st.session_state.get("_box_picker_receptor_pdb")
    if not receptor_raw:
        if st.button("Load receptor for box picking"):
            _handle_pick_box()
        return

    receptor_path = _resolve_container_path(receptor_raw)
    if not receptor_path.exists():
        st.warning("Prepared receptor no longer on disk — reload it.")
        if st.button("Reload receptor"):
            _handle_pick_box()
        return

    receptor_pdb = _read_file_bytes(
        str(receptor_path), receptor_path.stat().st_mtime
    ).decode("utf-8", errors="replace")

    s1, s2, s3 = st.columns(3)
    with s1:
        bx = st.number_input("Box size X", key="picker_box_x", value=20.0, min_value=_PICKER_MIN_BOX_SIZE, step=1.0)
    with s2:
        by = st.number_input("Box size Y", key="picker_box_y", value=20.0, min_value=_PICKER_MIN_BOX_SIZE, step=1.0)
    with s3:
        bz = st.number_input("Box size Z", key="picker_box_z", value=20.0, min_value=_PICKER_MIN_BOX_SIZE, step=1.0)

    center = st.session_state.get("picked_center")

    click = render_mol_viewer(
        receptor_pdb,
        mode="box_picker",
        center=center,
        box_size=[bx, by, bz],
        height=450,
        key=f"box_picker_{st.session_state.get('_box_picker_job_id')}",
    )
    if click and click.get("center") and click["center"] != center:
        center = click["center"]
        st.session_state["picked_center"] = center
        st.session_state["_box_picker_last_size"] = [bx, by, bz]
        _mirror_box_to_manual_inputs(center, [bx, by, bz])
        st.rerun()

    # Keep the manual fields (and hence the box actually docked with) in sync
    # if the user resizes after already having picked a center.
    if center and st.session_state.get("_box_picker_last_size") != [bx, by, bz]:
        st.session_state["_box_picker_last_size"] = [bx, by, bz]
        _mirror_box_to_manual_inputs(center, [bx, by, bz])

    if center:
        st.caption(f"Picked center: ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})")
    else:
        st.caption("Click a receptor atom in the viewer to set the box center.")

    if st.button("Discard picked receptor"):
        clear_box_picker_state()
        st.rerun()


def _render_docking_box() -> None:
    # on_change pushes manual edits into the box picker's own state, so the
    # 3D viewer follows typed values (the picker renders above these fields
    # and keeps separate state -- see _sync_picker_box_from_manual_inputs).
    # A callback runs before the next script run, i.e. before the picker's
    # widgets exist, so writing their keys there is allowed.
    sync = _sync_picker_box_from_manual_inputs
    st.markdown("**Docking Box**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.text_input("Center X", key="param_center_x", placeholder="0.0", on_change=sync)
    with c2:
        st.text_input("Center Y", key="param_center_y", placeholder="0.0", on_change=sync)
    with c3:
        st.text_input("Center Z", key="param_center_z", placeholder="0.0", on_change=sync)
    b1, b2, b3 = st.columns(3)
    with b1:
        st.text_input("Box X", key="param_box_x", placeholder="20.0", on_change=sync)
    with b2:
        st.text_input("Box Y", key="param_box_y", placeholder="20.0", on_change=sync)
    with b3:
        st.text_input("Box Z", key="param_box_z", placeholder="20.0", on_change=sync)


# ── Results panel ─────────────────────────────────────────────────────────────

def render_results_panel() -> None:
    st.subheader("Results")
    tab_job, tab_compare = st.tabs(["Session Viewer", "Compare Sessions"])

    with tab_job:
        _render_job_results()

    with tab_compare:
        comparison = st.session_state.get("comparison")
        if comparison:
            _render_comparison_table(comparison)
        else:
            st.caption("Select 2+ completed sessions in the sidebar and click Compare.")


def _load_workflow_results(job_id: str) -> dict | None:
    """Read workflow_results.json straight from disk for a completed job.

    Used to view jobs loaded from history (or run in a previous session /
    via the CLI), which have no in-memory OrchestratorBridge to poll.
    """
    results_file = OUTPUT_BASE / job_id / "workflow_results.json"
    if not results_file.exists():
        return None
    try:
        return json.loads(results_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_docking_progress(job_id: str) -> dict | None:
    path = OUTPUT_BASE / job_id / "docking_results" / "docking_progress.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _render_docking_progress(job_id: str) -> None:
    progress = _read_docking_progress(job_id)
    if not progress or progress.get("status") != "running":
        return

    total = progress.get("total") or 0
    completed = progress.get("completed") or 0
    if total:
        st.progress(completed / total, text=f"{completed} / {total} ligands docked")

    for entry in progress.get("in_progress") or []:
        ligand = entry.get("ligand")
        if not ligand:
            continue
        started_at = entry.get("started_at")
        elapsed_str = ""
        if started_at:
            try:
                started = datetime.fromisoformat(started_at)
                elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
                elapsed_str = f" (running {elapsed}s)"
            except ValueError:
                pass
        st.caption(f"Currently docking: `{ligand}`{elapsed_str}")


def _render_workflow_failure(job_id: str, result: dict) -> None:
    st.error(f"Session `{job_id}` failed.")
    for err in result.get("errors", []):
        st.caption(str(err))
    _render_job_log(job_id)


def _render_job_log(job_id: str) -> None:
    """Session Log viewer: reads job.log fresh every rerun (it's small and
    actively appended to while a job is running, so caching it -- unlike the
    other file reads in this module -- would just add complexity for no
    real benefit).
    """
    log_path = OUTPUT_BASE / job_id / "job.log"
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8", errors="replace")
    with st.expander("Session Log"):
        st.code("\n".join(text.splitlines()[-500:]))
        st.download_button(
            "Download full log", text, file_name="job.log", key=f"dl_job_log_{job_id}"
        )


def _job_label(job_id: str, job_name: str | None) -> str:
    """Markdown label for a job header: bold name (if any) plus the id."""
    if job_name:
        return f"**{escape_markdown(job_name)}** (`{job_id}`)"
    return f"`{job_id}`"


def _render_completed_or_failed(result: dict, job_id: str, job_label: str, *, live: bool) -> bool:
    """Render a completed/failed/cancelled result identically whether it
    came from a live bridge or a straight disk read. Returns True if the
    status was one this function handles, False so the caller can fall
    through (e.g. to a "no results" caption).

    Extracted so the live-bridge and disk-read branches of
    _render_job_results() can't silently diverge on what a given status
    renders, the way the live-bridge branch's error path once diverged from
    every other branch by skipping _render_job_log() (see
    docs/reviews/stage_11_2_11_3_r2.md).
    """
    status = result.get("status")
    if status == "completed":
        if live:
            st.success(f"Session {job_label} completed")
        else:
            st.info(f"Viewing session {job_label}")
        _render_affinity_chart(result)
        _render_visualization(result, job_id)
        _render_file_downloads(result, job_id)
        return True

    if status in ("failed", "cancelled"):
        _render_workflow_failure(job_id, result)
        return True

    return False


def _render_job_results() -> None:
    bridge: OrchestratorBridge = st.session_state.bridge
    active_job_id = st.session_state.job_id
    viewed_job_id = st.session_state.get("viewed_job_id") or active_job_id

    if not viewed_job_id:
        st.caption(
            "No session selected yet. Configure parameters and click Run Docking, "
            "or click Load next to a session in the sidebar's Session History."
        )
        return

    # The session currently tracked by the live bridge takes priority — it
    # may still be running, in which case there's nothing on disk to fall
    # back to.
    if bridge and viewed_job_id == active_job_id:
        job_label = _job_label(viewed_job_id, st.session_state.get("job_name"))

        if bridge.is_running():
            st.info(f"Session {job_label} is running...")
            _render_docking_progress(viewed_job_id)
            if bridge.stop_requested():
                st.caption("Stop requested — stopping (docking halts within a few seconds)...")
            elif st.button("Stop", key=f"stop_{viewed_job_id}"):
                bridge.request_stop()
                st.rerun()
            _render_job_log(viewed_job_id)
            return

        result = bridge.get_result()
        error = bridge.get_error()

        if error:
            st.error(f"Session failed: {error}")
            _render_job_log(viewed_job_id)
            return

        if result and _render_completed_or_failed(result, viewed_job_id, job_label, live=True):
            return

    # Otherwise, view a session by reading its results straight from disk —
    # covers sessions Loaded from history and sessions with no live bridge.
    result = _load_workflow_results(viewed_job_id)
    if result:
        job_label = _job_label(viewed_job_id, result.get("job_name"))
        if _render_completed_or_failed(result, viewed_job_id, job_label, live=False):
            return

    st.caption("No results found for this session.")


def _render_affinity_chart(result: dict) -> None:
    dock_results = result.get("results", {}).get("dock", {}).get("results", [])
    fig = make_affinity_chart_figure(dock_results)
    if fig:
        with st.container(height=400):
            st.plotly_chart(fig, use_container_width=True)


def _resolve_container_path(path: str) -> Path:
    """Remap /app/orchestrator_output/... → OUTPUT_BASE/... so the UI container
    can access files written by MCP services that mount the volume at /app.

    Thin wrapper over :func:`common.path_utils.from_container_path` -- the
    CLI's `nlp_orchestrator.py` needs the same "MCP service's own mount" ->
    "this process's local mount" translation for the same reason (Stage 12.3).
    """
    return from_container_path(path, OUTPUT_BASE)


def _split_sdf_records(sdf_text: str) -> list[str]:
    """Split a multi-record SDF (one $$$$-terminated block per pose) into a
    list of individually valid single-record SDF texts, in file order."""
    blocks = re.split(r"\r?\n\$\$\$\$\r?\n?", sdf_text)
    return [block + "\n$$$$\n" for block in blocks if block.strip()]


def _render_visualization(result: dict, job_id: str) -> None:
    viz = result.get("results", {}).get("visualize", {})
    raw_receptor = viz.get("receptor_pdb")
    if not raw_receptor:
        _render_job_log(job_id)
        return
    receptor_path = _resolve_container_path(raw_receptor)
    if not receptor_path.exists():
        st.caption("Receptor structure not found on disk.")
        _render_job_log(job_id)
        return
    receptor_pdb = _read_file_bytes(
        str(receptor_path), receptor_path.stat().st_mtime
    ).decode("utf-8", errors="replace")

    dock_results = {
        r["ligand_id"]: r
        for r in result.get("results", {}).get("dock", {}).get("results", [])
        if r.get("ligand_id")
    }

    # Parse each ligand's converted poses and read the *currently selected*
    # pose straight from session_state (set by the pickers rendered further
    # down) so the viewer can be drawn right here, immediately below the bar
    # chart — instead of after a page-long list of per-ligand pose pickers,
    # which with a few hundred ligands buried the viewer many screens down.
    multi_pose: dict[str, list[str]] = {}
    ligands: dict[str, dict] = {}
    for entry in viz.get("converted", []):
        ligand_id = entry.get("ligand_id")
        raw_sdf_path = entry.get("sdf_path")
        if not ligand_id or not raw_sdf_path:
            continue
        sdf_path = _resolve_container_path(raw_sdf_path)
        if not sdf_path.exists():
            continue
        sdf_text = _read_file_bytes(
            str(sdf_path), sdf_path.stat().st_mtime
        ).decode("utf-8", errors="replace")
        records = _split_sdf_records(sdf_text)
        if not records:
            continue
        if len(records) > 1:
            multi_pose[ligand_id] = records

        modes = dock_results.get(ligand_id, {}).get("modes") or []
        selected = st.session_state.get(f"pose_select_{job_id}_{ligand_id}", 1)
        pose_index = min(selected - 1, len(records) - 1)

        affinity = (
            modes[pose_index].get("affinity")
            if pose_index < len(modes)
            else dock_results.get(ligand_id, {}).get("best_affinity")
        )
        ligands[ligand_id] = {"sdf": records[pose_index], "affinity": affinity}

    render_mol_viewer(receptor_pdb, ligands, mode="view", height=550, key=f"mol_viewer_{job_id}")

    _render_job_log(job_id)

    if multi_pose:
        st.caption("Pose per ligand")
        with st.container(height=300):
            for ligand_id, records in multi_pose.items():
                modes = dock_results.get(ligand_id, {}).get("modes") or []
                options = list(range(1, len(records) + 1))

                def _format_pose(i: int, m=modes) -> str:
                    if i - 1 < len(m) and m[i - 1].get("affinity") is not None:
                        return f"Mode {i} ({m[i - 1]['affinity']:.2f} kcal/mol)"
                    return f"Mode {i}"

                st.selectbox(
                    f"{ligand_id} — pose",
                    options,
                    format_func=_format_pose,
                    key=f"pose_select_{job_id}_{ligand_id}",
                )


@st.cache_data(show_spinner=False)
def _read_file_bytes(path_str: str, mtime: float) -> bytes:
    # mtime is part of the cache key purely to invalidate correctly if a
    # file is regenerated -- the read itself is what's expensive to repeat.
    # This renders on every Streamlit rerun, including the 2s active-job
    # poll loop, so without caching every job file gets re-read from disk
    # every ~2s regardless of whether the user ever clicks a download button.
    return Path(path_str).read_bytes()


_POSE_DOWNLOAD_FORMATS = ["pdbqt", "pdb", "sdf", "mol2"]


def _render_pose_downloads(result: dict, job_id: str) -> None:
    """Per-ligand docked-pose downloads with a global format picker.

    PDBQT is Moldina's native output (no conversion, always available if
    docking succeeded for that ligand) and is the default; PDB/SDF/MOL2 are
    the whole multi-pose file (all modes) written by the visualize step's
    convert_docked_poses, in whichever formats it was asked to produce.
    """
    dock_results = {
        r["ligand_id"]: r
        for r in result.get("results", {}).get("dock", {}).get("results", [])
        if r.get("ligand_id")
    }
    if not dock_results:
        return

    converted = {
        entry["ligand_id"]: entry
        for entry in result.get("results", {}).get("visualize", {}).get("converted", [])
        if entry.get("ligand_id")
    }

    st.subheader("Docked Poses")
    fmt = st.selectbox("Download format", _POSE_DOWNLOAD_FORMATS, index=0, key=f"dl_format_{job_id}")

    with st.container(height=300):
        for ligand_id, dock_entry in dock_results.items():
            if fmt == "pdbqt":
                raw_path = dock_entry.get("output_path")
            else:
                raw_path = converted.get(ligand_id, {}).get(f"{fmt}_path")

            if not raw_path:
                continue
            path = _resolve_container_path(raw_path)
            if not path.exists():
                continue

            data = _read_file_bytes(str(path), path.stat().st_mtime)
            st.download_button(
                f"Download {ligand_id} ({fmt})",
                data,
                file_name=path.name,
                key=f"dl_pose_{job_id}_{ligand_id}_{fmt}",
            )


def _render_file_downloads(result: dict, job_id: str) -> None:
    if not job_id:
        return
    job_dir = OUTPUT_BASE / job_id
    if not job_dir.exists():
        return

    _render_pose_downloads(result, job_id)

    st.subheader("Other Files")
    for f in sorted(job_dir.rglob("*")):
        if f.is_file() and f.suffix in {".pml", ".json", ".png"}:
            data = _read_file_bytes(str(f), f.stat().st_mtime)
            st.download_button(
                f"Download {f.name}",
                data,
                file_name=f.name,
                key=f"dl_{f}",
            )


def _render_comparison_table(comparison: dict) -> None:
    st.markdown(build_comparison_html(comparison), unsafe_allow_html=True)
    st.caption("Green = strongest predicted binding (most negative kcal/mol). N/A = not docked in that session.")


# ── Layout ────────────────────────────────────────────────────────────────────

render_sidebar()

chat_col, params_col, results_col = st.columns([35, 25, 40])

with chat_col:
    render_chat()

with params_col:
    render_parameters_panel()

with results_col:
    render_results_panel()

# Keep polling while a docking job or the box-picker's receptor prep is in
# progress — 2 s delay avoids spinning the CPU.
box_picker_bridge = st.session_state.get("box_picker_bridge")
if (st.session_state.bridge and st.session_state.bridge.is_running()) or \
        (box_picker_bridge and box_picker_bridge.is_running()):
    import time
    time.sleep(2)
    st.rerun()
