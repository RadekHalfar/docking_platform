"""Streamlit front-end for the Molecular Docking Platform."""
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from common.docking_algorithm import DOCKING_ALGORITHMS, docking_algorithm_label
from common.path_utils import from_container_path
from services.llm_orchestrator.job_comparator import compare_jobs
from services.llm_orchestrator.rerun_planner import describe_rerun_plan
from services.llm_orchestrator.workflow_llm_parser import (
    parse_followup_with_llm,
    parse_workflow_with_llm,
)
from ui.core import form as gui_form
from ui.core import box_picker, chat, results_view, run_control, sessions
from ui.core import save_results as gui_save
from ui.job_history import escape_markdown, list_completed_jobs, list_failed_jobs
from ui.mol_viewer import render_mol_viewer
from ui.ollama_models import list_ollama_models
from ui.orchestrator_bridge import OrchestratorBridge
from ui.results import build_comparison_html, make_affinity_chart_figure
from ui.session_state import (
    WORKER_CAP,
    apply_settings,
    clear_box_picker_state,
    effective_cpu,
    gui_session,
    init,
    reset_workers_to_algorithm_default,
    selected_docking_algorithm,
    start_new_session,
)
from ui.worker_cap import describe_worker_cap, max_workers_for

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
    """See :func:`ui.core.form.collect_form_params`."""
    return gui_form.collect_form_params(gui_session())


def _show_outcome(outcome) -> None:
    """Carry out a ``ui/core`` action's :class:`~ui.core.state.Outcome`:
    an inline error, then a rerun if the page must redraw."""
    if outcome.error:
        st.error(outcome.error)
    if outcome.refresh:
        st.rerun()


# ── Chat handler ──────────────────────────────────────────────────────────────

# The logic lives in ui/core (chat, box_picker). The LLM parse functions
# are passed at call time, so a test patching them on this module reaches
# them. The two geometry wrappers below are kept only for the
# tests/test_ui_app_helpers.py cases that call them; the chat route itself
# goes through chat.submit(), which starts the prep via ui.core.box_picker.

def _find_receptor_pdb_for_geometry() -> Path | None:
    """See :func:`ui.core.box_picker.find_receptor_pdb`."""
    return box_picker.find_receptor_pdb(gui_session(), OUTPUT_BASE)


def _handle_protein_geometry_command(target: str) -> None:
    """See :func:`ui.core.chat.protein_geometry_command`."""
    chat.protein_geometry_command(gui_session(), target, OUTPUT_BASE, _start_box_picker_prep)


def _on_chat_submit(user_message: str) -> None:
    state = gui_session()
    if chat.submit(state, user_message, OUTPUT_BASE, OrchestratorBridge):
        return
    with st.spinner("Parsing with LLM..."):
        chat.answer_with_llm(
            state,
            user_message,
            parse_workflow=parse_workflow_with_llm,
            parse_followup=parse_followup_with_llm,
        )


# ── Run / Reset ───────────────────────────────────────────────────────────────
# The actions live in ui/core (run_control, sessions); these wrappers carry
# out their Outcome the Streamlit way (st.error / st.rerun).

def _handle_run_docking() -> None:
    _show_outcome(run_control.run_docking(gui_session(), OUTPUT_BASE, OrchestratorBridge))


def _handle_save_session() -> None:
    _show_outcome(run_control.save_session(gui_session()))


def _execute_confirmed_redock() -> None:
    _show_outcome(run_control.confirm_redock(gui_session(), OUTPUT_BASE, OrchestratorBridge))


def _undo_to_revision(revision: int) -> str:
    """See :func:`ui.core.sessions.undo_to_revision`. Never calls st.rerun()
    and must run before the parameter widgets render (it rewrites their keys)."""
    return sessions.undo_to_revision(gui_session(), revision)


_SAVE_SCOPES = gui_save.SAVE_SCOPES


def _save_block_reason() -> str | None:
    """See :func:`ui.core.save_results.save_block_reason`."""
    return gui_save.save_block_reason(gui_session(), OUTPUT_BASE)


def _save_results(name: str, scope: str, *, overwrite: bool = False) -> str:
    """See :func:`ui.core.save_results.save_results`. Never calls st.rerun()."""
    return gui_save.save_results(gui_session(), OUTPUT_BASE, name, scope, overwrite=overwrite)


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


def _resume_session(job: dict) -> None:
    """"Load" -- see :func:`ui.core.sessions.resume_session`."""
    sessions.resume_session(gui_session(), job, OUTPUT_BASE)
    st.rerun()


def _clear_all_job_caches() -> None:
    _cached_completed_jobs.clear()
    _cached_failed_jobs.clear()


def _active_bridge_job_id() -> str | None:
    """See :func:`ui.core.run_control.active_bridge_job_id`."""
    return run_control.active_bridge_job_id(gui_session())


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
                error = sessions.rename_session(gui_session(), job, new_name)
                if error:
                    st.error(error)
                else:
                    _clear_all_job_caches()
                    st.session_state[f"_renaming_{job['job_id']}"] = False
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
                error = sessions.delete_session(gui_session(), job)
                if error:
                    st.error(error)
                else:
                    _clear_all_job_caches()
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

def _process_docking_bridge() -> None:
    """See :func:`ui.core.run_control.process_docking_bridge`. Runs before
    the layout (see ``_process_bridges``)."""
    run_control.process_docking_bridge(gui_session())


def _process_box_picker_bridge() -> None:
    """See :func:`ui.core.box_picker.process_box_picker_bridge`. Runs before
    the layout (see ``_process_bridges``), so the param_* keys it writes are
    set before their widgets exist, and its chat reply is drawn this same run."""
    box_picker.process_box_picker_bridge(gui_session(), OUTPUT_BASE)


def _any_bridge_running() -> bool:
    return run_control.any_bridge_running(gui_session())


def _process_bridges() -> bool:
    """Fold both background bridges' progress/outcome into session state
    before anything is drawn. Called ahead of the layout -- even ahead of
    the sidebar, whose buttons can cut a run short with st.rerun() -- so a
    finished job is handled in the same run that first sees it, and the
    replies it posts are in ``messages`` before the chat history is drawn
    (at the end of the script, see ``_render_chat_history``).

    Returns whether a bridge was still running at this point. The page
    must then poll once more even if the job has finished by the bottom of
    the script: this is the only place a finished job is handled, so a job
    that ends mid-run would otherwise sit unhandled (no reply, no results)
    until the user's next click.
    """
    running = _any_bridge_running()
    _process_docking_bridge()
    _process_box_picker_bridge()
    return running


def render_chat():
    """Chat column: header, an (empty) history container and the input.

    Returns the history container; the module bottom fills it via
    ``_render_chat_history`` once every panel has run, so a reply appended
    anywhere during this run -- a chat command, a button in another column,
    a finished bridge -- shows now, not after the next interaction.
    """
    job_id = st.session_state.get("job_id")
    if job_id:
        st.caption(f"Session: {_job_label(job_id, st.session_state.get('job_name'))}")
    st.subheader("Chat")

    history = st.container(height=1000)

    if prompt := st.chat_input("Describe your workflow..."):
        _on_chat_submit(prompt)
    return history


def _render_chat_history(container) -> None:
    with container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])


# ── Parameters panel ──────────────────────────────────────────────────────────

_PICKER_DEFAULT_BOX_SIZE = box_picker.PICKER_DEFAULT_BOX_SIZE

_BOX_WIDGET_KEYS = gui_form.BOX_FORM_KEYS


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
    # click/size step, chat-parsed values, geometry commands), whereas the
    # params lists only change on chat parsing or a run -- preferring them
    # left the summary stale after a visual pick or resize.
    def _coord(widget_keys: tuple) -> str:
        parts = [str(st.session_state.get(k, "")).strip() for k in widget_keys]
        return " / ".join(p if p else "—" for p in parts)

    center = _coord(_BOX_WIDGET_KEYS[:3])
    box = _coord(_BOX_WIDGET_KEYS[3:])

    # The selector, not params: like the box fields, it is what docks.
    algorithm = selected_docking_algorithm()

    with st.expander("Current parameters", expanded=True):
        st.markdown(
            f"**Protein:** {protein}  \n"
            f"**Ligands:** {ligands}  \n"
            f"**Center:** {center}  \n"
            f"**Box:** {box}  \n"
            f"**Algorithm:** {docking_algorithm_label(algorithm)}"
        )


def _on_restore_revision_click() -> None:
    # A callback, not an inline `if st.button(...)`: restoring rewrites the
    # param_* widget keys, which is only allowed before those widgets
    # render -- inline, this panel's widgets already exist.
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

    revisions = state_manager.list_revisions()
    with st.expander("Revision history"):
        st.markdown(sessions.format_history_markdown(revisions))
        current = state_manager.get_parameters_revision()
        if current is not None:
            st.caption(f"Parameters are currently at revision {current}.")

        param_revisions = [
            r["revision"] for r in reversed(revisions) if r["has_parameters"]
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
            width="stretch",
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
    # change the Docking Box fields during this run (a viewer click), and
    # the summary must reflect that, not the pre-run values.
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
        algorithm = st.radio(
            "Docking algorithm",
            options=DOCKING_ALGORITHMS,
            format_func=docking_algorithm_label,
            key="param_docking_algorithm",
            horizontal=True,
            on_change=reset_workers_to_algorithm_default,
        )
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("pH", key="param_ph")
        with col2:
            st.text_input(
                "Exhaustiveness",
                key="param_exhaustiveness",
                help="Vina (Monte Carlo) only — Moldina (PSO) ignores it.",
            )
        if algorithm == "vina":
            st.text_input("CPUs per ligand", key="param_cpu")
            st.caption(
                "Vina's search is multithreaded: more CPUs per ligand or a lower "
                "exhaustiveness make each ligand finish sooner. Workers × CPUs per "
                "ligand never exceeds the docking service's CPU limit."
            )
        else:
            st.caption(
                "Moldina (PSO) uses 1 CPU per ligand and ignores Exhaustiveness."
            )

        st.checkbox("Parallel ligand docking", key="param_parallel")
        # The server clamps workers to its CPU quota // CPUs per ligand; a
        # value above the widget's max would make st.number_input raise, so
        # clamp it first (e.g. after raising CPUs per ligand).
        max_workers = max_workers_for(WORKER_CAP, effective_cpu())
        if st.session_state.get("param_max_workers", 1) > max_workers:
            st.session_state["param_max_workers"] = max_workers
        st.number_input(
            "Max parallel workers",
            key="param_max_workers",
            min_value=1,
            max_value=max_workers,
            disabled=not st.session_state.get("param_parallel", False),
        )
        cap_warning, cap_caption = describe_worker_cap(WORKER_CAP, algorithm)
        if cap_warning:
            st.warning(cap_warning)
        st.caption(cap_caption)

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
            if st.button("Confirm redock", type="primary", width="stretch"):
                _execute_confirmed_redock()
        with col_cancel:
            if st.button("Cancel", width="stretch"):
                _show_outcome(run_control.cancel_redock(gui_session()))
    else:
        # New Session/Reset both detach st.session_state from whatever job
        # is currently attached -- while this tab's own bridge is actively
        # running that job, detaching would orphan the background thread
        # (nothing left in session_state could observe or stop it) and
        # desync the sidebar's running-job guards for it (see
        # ui.core.run_control.active_bridge_job_id's docstring).
        running_job = _active_bridge_job_id()
        col_run, col_save, col_new, col_reset = st.columns(4)
        with col_run:
            if st.button("Run Docking", type="primary", width="stretch"):
                _handle_run_docking()
        with col_save:
            if st.button(
                "Save Session",
                width="stretch",
                disabled=not st.session_state.interactive_session.has_active_job(),
                help="Save current parameters without docking.",
            ):
                _handle_save_session()
        with col_new:
            if st.button(
                "New Session",
                width="stretch",
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
                width="stretch",
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


def _read_box_fields() -> tuple[list[float] | None, list[float] | None]:
    """See :func:`ui.core.form.read_box_fields`."""
    return gui_form.read_box_fields(gui_session())


def _drawable_box_size() -> list[float] | None:
    """See :func:`ui.core.box_picker.drawable_box_size`."""
    return box_picker.drawable_box_size(gui_session())


def _apply_viewer_click(center: list) -> None:
    """See :func:`ui.core.box_picker.apply_viewer_click`. Must run before
    _render_docking_box() instantiates those widgets."""
    box_picker.apply_viewer_click(gui_session(), center)


def _step_box_size(axis: int, delta: float) -> None:
    """See :func:`ui.core.box_picker.step_box_size` (an on_click callback)."""
    box_picker.step_box_size(gui_session(), axis, delta)


def _start_box_picker_prep() -> tuple[bool, str | None, str]:
    """See :func:`ui.core.box_picker.start_box_picker_prep`."""
    return box_picker.start_box_picker_prep(gui_session(), OUTPUT_BASE, OrchestratorBridge)


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

    # Set by _process_box_picker_bridge() when the prep job failed; shown
    # once, like an inline st.error would be.
    if panel_error := st.session_state.pop("_box_picker_error", None):
        st.error(panel_error)

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

    # The viewer draws the Docking Box fields -- the only box state; a click
    # or a − / + step writes them. Nothing here keeps a copy to drift from.
    center, _ = _read_box_fields()
    box_size = _drawable_box_size()

    # One row of six (not three columns of two): Streamlit advises against
    # nesting columns more than once, and this panel is already a column.
    step_cols = iter(st.columns(6))
    for axis, name in enumerate("XYZ"):
        for delta, sign, verb, suffix in ((-1.0, "−", "Shrink", "down"), (1.0, "+", "Grow", "up")):
            next(step_cols).button(
                f"{name} {sign}", key=f"box_step_{name.lower()}_{suffix}", width="stretch",
                on_click=_step_box_size, args=(axis, delta),
                help=f"{verb} box {name} by 1 Å",
            )

    viewer_key = f"box_picker_{st.session_state.get('_box_picker_job_id')}"
    click = render_mol_viewer(
        receptor_pdb,
        mode="box_picker",
        center=center,
        # The placeholder is passed explicitly rather than left to the
        # component's own default, so it is the same constant a click or
        # − / + writes into the fields.
        box_size=box_size or [_PICKER_DEFAULT_BOX_SIZE] * 3,
        height=450,
        key=viewer_key,
    )
    # The component returns its last click on every run until the next one;
    # each click carries a fresh clickId, so comparing the whole value with
    # the last one applied tells a new click (even on the same atom) from a
    # repeat -- and a repeat must not overwrite a center typed since.
    if click and click.get("center") and click != st.session_state.get("_box_picker_last_click"):
        st.session_state["_box_picker_last_click"] = click
        _apply_viewer_click(click["center"])
        st.rerun()

    if center:
        st.caption(f"Box center: ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})")
    else:
        st.caption("Click a receptor atom in the viewer to set the box center.")
    if box_size is None:
        st.caption(
            f"Box size is not set — the viewer shows a {_PICKER_DEFAULT_BOX_SIZE:g} Å "
            "placeholder, which a click or − / + fills in."
        )

    if st.button("Discard picked receptor"):
        clear_box_picker_state()
        st.rerun()


def _render_docking_box() -> None:
    st.markdown("**Docking Box**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.text_input("Center X", key="param_center_x", placeholder="0.0")
    with c2:
        st.text_input("Center Y", key="param_center_y", placeholder="0.0")
    with c3:
        st.text_input("Center Z", key="param_center_z", placeholder="0.0")
    b1, b2, b3 = st.columns(3)
    with b1:
        st.text_input("Box X", key="param_box_x", placeholder="20.0")
    with b2:
        st.text_input("Box Y", key="param_box_y", placeholder="20.0")
    with b3:
        st.text_input("Box Z", key="param_box_z", placeholder="20.0")


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
    """See :func:`ui.core.sessions.load_workflow_results`."""
    return sessions.load_workflow_results(OUTPUT_BASE, job_id)


def _read_docking_progress(job_id: str) -> dict | None:
    """See :func:`ui.core.results_view.read_docking_progress`."""
    return results_view.read_docking_progress(OUTPUT_BASE, job_id)


def _render_docking_progress(job_id: str) -> None:
    progress = _read_docking_progress(job_id)
    if not progress or progress.get("status") != "running":
        return

    total = progress.get("total") or 0
    completed = progress.get("completed") or 0
    if total:
        st.progress(completed / total, text=f"{completed} / {total} ligands docked")

    for ligand, elapsed in results_view.currently_docking(progress, datetime.now(timezone.utc)):
        elapsed_str = f" (running {elapsed}s)" if elapsed is not None else ""
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
    text = results_view.read_job_log(OUTPUT_BASE, job_id)
    if text is None:
        return
    with st.expander("Session Log"):
        st.code("\n".join(text.splitlines()[-results_view.JOB_LOG_TAIL_LINES:]))
        st.download_button(
            "Download full log", text, file_name="job.log", key=f"dl_job_log_{job_id}"
        )


def _job_label(job_id: str, job_name: str | None) -> str:
    """See :func:`ui.core.results_view.job_label`."""
    return results_view.job_label(job_id, job_name)


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
        _render_docking_algorithm_caption(result)
        _render_affinity_chart(result)
        _render_visualization(result, job_id)
        _render_file_downloads(result, job_id)
        return True

    if status in ("failed", "cancelled"):
        _render_workflow_failure(job_id, result)
        return True

    return False


# The Save results buttons are on_click callbacks so that the save (and any
# _save_conflict it records) happens before this panel renders: the
# Overwrite / Save as buttons and the block-reason caption then reflect it in
# the same run. (Their chat replies would show either way -- the history is
# drawn last.)

def _on_save_results_click() -> None:
    name = (st.session_state.get("save_bundle_name") or "").strip()
    reply = _save_results(name, st.session_state.get("save_scope", "all"))
    st.session_state.messages.append({"role": "assistant", "content": reply})


def _on_save_conflict_click(*, overwrite: bool) -> None:
    reply = gui_save.resolve_save_conflict(gui_session(), OUTPUT_BASE, overwrite=overwrite)
    if reply is not None:
        st.session_state.messages.append({"role": "assistant", "content": reply})


def _on_save_conflict_cancel() -> None:
    gui_save.cancel_save_conflict(gui_session())


def _render_save_results(job_id: str) -> None:
    """Save results as… controls, for the active session only (a session
    viewed but not active has no state manager to read revision/params from)."""
    session = st.session_state.interactive_session
    if not session.has_active_job() or session.job_id != job_id:
        return

    with st.expander("Save results as…"):
        st.caption(
            "Copies this session's results into "
            "`saved_results/<name>/` inside the session folder."
        )
        st.text_input("Bundle name", key="save_bundle_name", placeholder="e.g. trial_a")
        st.selectbox(
            "Include", _SAVE_SCOPES, key="save_scope",
            format_func=gui_save.SCOPE_LABELS.__getitem__,
        )
        reason = _save_block_reason()
        st.button(
            "Save results bundle",
            on_click=_on_save_results_click,
            disabled=reason is not None,
            width="stretch",
        )
        if reason:
            st.caption(reason)

        conflict = st.session_state.get("_save_conflict")
        if conflict and conflict["job_id"] == job_id:
            st.warning(f"A bundle named `{conflict['name']}` already exists.")
            col_over, col_new, col_cancel = st.columns(3)
            col_over.button(
                "Overwrite", on_click=_on_save_conflict_click, kwargs={"overwrite": True},
                key="save_conflict_overwrite",
            )
            col_new.button(
                f"Save as {conflict['suggested']}", on_click=_on_save_conflict_click,
                kwargs={"overwrite": False}, key="save_conflict_new_name",
            )
            col_cancel.button("Cancel", on_click=_on_save_conflict_cancel, key="save_conflict_cancel")


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
                _show_outcome(run_control.stop_run(gui_session()))
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


def _render_docking_algorithm_caption(result: dict) -> None:
    caption = results_view.docked_with_caption(result)
    if caption:
        st.caption(caption)


def _render_affinity_chart(result: dict) -> None:
    dock_results = result.get("results", {}).get("dock", {}).get("results", [])
    fig = make_affinity_chart_figure(dock_results)
    if fig:
        with st.container(height=400):
            st.plotly_chart(fig, width="stretch")


def _resolve_container_path(path: str) -> Path:
    """Remap /app/orchestrator_output/... → OUTPUT_BASE/... so the UI container
    can access files written by MCP services that mount the volume at /app.

    Thin wrapper over :func:`common.path_utils.from_container_path` -- the
    CLI's `nlp_orchestrator.py` needs the same "MCP service's own mount" ->
    "this process's local mount" translation for the same reason (Stage 12.3).
    """
    return from_container_path(path, OUTPUT_BASE)


def _read_cached(path: Path) -> bytes:
    """``results_view``'s file reader, through the ``_read_file_bytes`` cache."""
    return _read_file_bytes(str(path), path.stat().st_mtime)


def _render_save_and_log(job_id: str) -> None:
    # Directly below the viewer, above the (long) per-ligand pose pickers.
    _render_save_results(job_id)
    _render_job_log(job_id)


def _render_visualization(result: dict, job_id: str) -> None:
    viz = result.get("results", {}).get("visualize", {})
    raw_receptor = viz.get("receptor_pdb")
    if not raw_receptor:
        _render_save_and_log(job_id)
        return
    receptor_path = _resolve_container_path(raw_receptor)
    if not receptor_path.exists():
        st.caption("Receptor structure not found on disk.")
        _render_save_and_log(job_id)
        return
    receptor_pdb = _read_file_bytes(
        str(receptor_path), receptor_path.stat().st_mtime
    ).decode("utf-8", errors="replace")

    dock_results = results_view.dock_results_by_ligand(result)

    # Parse each ligand's converted poses and read the *currently selected*
    # pose straight from session_state (set by the pickers rendered further
    # down) so the viewer can be drawn right here, immediately below the bar
    # chart — instead of after a page-long list of per-ligand pose pickers,
    # which with a few hundred ligands buried the viewer many screens down.
    records_by_ligand = results_view.pose_records(result, OUTPUT_BASE, _read_cached)
    multi_pose = {lig: recs for lig, recs in records_by_ligand.items() if len(recs) > 1}
    ligands = {
        ligand_id: results_view.viewer_ligand(
            records,
            dock_results.get(ligand_id, {}),
            st.session_state.get(f"pose_select_{job_id}_{ligand_id}", 1),
        )
        for ligand_id, records in records_by_ligand.items()
    }

    render_mol_viewer(receptor_pdb, ligands, mode="view", height=550, key=f"mol_viewer_{job_id}")

    _render_save_and_log(job_id)

    if multi_pose:
        st.caption("Pose per ligand")
        with st.container(height=300):
            for ligand_id, records in multi_pose.items():
                modes = dock_results.get(ligand_id, {}).get("modes") or []
                options = list(range(1, len(records) + 1))

                def _format_pose(i: int, m=modes) -> str:
                    return results_view.pose_label(m, i)

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


_POSE_DOWNLOAD_FORMATS = results_view.POSE_DOWNLOAD_FORMATS


def _render_pose_downloads(result: dict, job_id: str) -> None:
    """Per-ligand docked-pose downloads with a global format picker (see
    :func:`ui.core.results_view.pose_download_paths`); PDBQT is the default."""
    if not results_view.dock_results_by_ligand(result):
        return

    st.subheader("Docked Poses")
    fmt = st.selectbox("Download format", _POSE_DOWNLOAD_FORMATS, index=0, key=f"dl_format_{job_id}")

    with st.container(height=300):
        for ligand_id, path in results_view.pose_download_paths(result, OUTPUT_BASE, fmt):
            st.download_button(
                f"Download {ligand_id} ({fmt})",
                _read_cached(path),
                file_name=path.name,
                key=f"dl_pose_{job_id}_{ligand_id}_{fmt}",
            )


def _render_file_downloads(result: dict, job_id: str) -> None:
    if not job_id:
        return
    if not (OUTPUT_BASE / job_id).exists():
        return

    _render_pose_downloads(result, job_id)

    st.subheader("Other Files")
    for f in results_view.other_download_files(OUTPUT_BASE, job_id):
        st.download_button(
            f"Download {f.name}",
            _read_cached(f),
            file_name=f.name,
            key=f"dl_{f}",
        )


def _render_comparison_table(comparison: dict) -> None:
    st.markdown(build_comparison_html(comparison), unsafe_allow_html=True)
    st.caption("Green = strongest predicted binding (most negative kcal/mol). N/A = not docked in that session.")


# ── Layout ────────────────────────────────────────────────────────────────────

bridge_was_running = _process_bridges()

render_sidebar()

chat_col, params_col, results_col = st.columns([35, 25, 40])

with chat_col:
    chat_history = render_chat()

with params_col:
    render_parameters_panel()

with results_col:
    render_results_panel()

# Drawn last, into the container render_chat() placed at the top of the chat
# column: any code in this run may append to st.session_state.messages (a chat
# command, a button in another column, a finished bridge) and the reply shows
# now -- no st.rerun() just to make a reply visible. Must stay above the
# polling rerun below.
_render_chat_history(chat_history)

# Keep polling while a docking job or the box-picker's receptor prep is in
# progress — 2 s delay avoids spinning the CPU. One more poll after a job
# that was running at the top of this run has finished, so
# _process_bridges() handles it (see there).
if bridge_was_running or _any_bridge_running():
    import time
    time.sleep(2)
    st.rerun()
