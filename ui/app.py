"""Streamlit front-end for the Molecular Docking Platform."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from common.utils import generate_job_id
from services.llm_orchestrator.job_comparator import compare_jobs
from services.llm_orchestrator.workflow_llm_parser import parse_workflow_with_llm
from ui.job_history import list_completed_jobs
from ui.ollama_models import list_ollama_models
from ui.orchestrator_bridge import OrchestratorBridge
from ui.results import build_comparison_html, make_affinity_chart_figure
from ui.session_state import apply_parsed_params, apply_settings, get_params, init

st.set_page_config(
    layout="wide",
    page_title="Molecular Docking Platform",
    page_icon="🧬",
)

init()

OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "/workspace/orchestrator_output"))
UPLOAD_DIR = OUTPUT_BASE / "uploads"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_upload(uploaded_file, dest: Path) -> str:
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
        ("cpu", "4", int),
    ]:
        try:
            params[key] = cast(st.session_state.get(f"param_{key}", default))
        except (ValueError, TypeError):
            pass

    return params


# ── Chat handler ──────────────────────────────────────────────────────────────

def _on_chat_submit(user_message: str) -> None:
    st.session_state.messages.append({"role": "user", "content": user_message})
    with st.spinner("Parsing with LLM..."):
        try:
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
    st.session_state.job_id = job_id
    st.session_state.viewed_job_id = job_id
    st.session_state["_job_done"] = False

    bridge = OrchestratorBridge()
    bridge.start_workflow(validated, job_id, str(OUTPUT_BASE))
    st.session_state.bridge = bridge

    st.session_state.messages.append(
        {"role": "assistant", "content": f"Starting docking job `{job_id}`..."}
    )
    st.rerun()


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────

_PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openai_compatible": "Other local server",
}
_PROVIDER_KEYS = {label: key for key, label in _PROVIDER_LABELS.items()}


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Settings")
        _render_settings_form()

        st.divider()
        st.subheader("Job History")
        _render_job_history()


@st.cache_data(ttl=30, show_spinner=False)
def _cached_ollama_models(endpoint: str) -> list:
    return list_ollama_models(endpoint)


def _render_settings_form() -> None:
    settings = st.session_state.settings
    labels = list(_PROVIDER_LABELS.values())
    current_label = _PROVIDER_LABELS.get(settings.get("provider", "ollama"), labels[0])

    provider_label = st.selectbox("LLM Provider", labels, index=labels.index(current_label))
    provider = _PROVIDER_KEYS[provider_label]

    endpoint = settings.get("endpoint", "")
    base_url = settings.get("base_url", "")
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

    if provider in ("openai", "anthropic", "openai_compatible"):
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


def _render_job_history() -> None:
    jobs = list_completed_jobs(str(OUTPUT_BASE))

    if not jobs:
        st.caption("No completed jobs yet.")
        return

    selected = []
    for job in jobs[:10]:
        label = f"{job['job_id']}  {job['protein_id']} ({job['ligand_count']} ligand(s))"
        col_check, col_load = st.columns([4, 1])
        with col_check:
            if st.checkbox(label, key=f"job_sel_{job['job_id']}"):
                selected.append(job["job_dir"])
        with col_load:
            if st.button("Load", key=f"job_load_{job['job_id']}"):
                st.session_state.viewed_job_id = job["job_id"]
                st.rerun()

    if len(selected) >= 2 and st.button("Compare selected jobs"):
        try:
            st.session_state.comparison = compare_jobs(selected)
        except Exception as exc:
            st.error(f"Comparison failed: {exc}")


# ── Chat panel ────────────────────────────────────────────────────────────────

def render_chat() -> None:
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
        if result and result.get("status") == "completed" and not st.session_state.get("_job_done"):
            st.session_state["_job_done"] = True
            st.session_state.messages.append(
                {"role": "assistant", "content": "Workflow completed successfully!"}
            )
        elif error and not st.session_state.get("_job_done"):
            st.session_state["_job_done"] = True
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Workflow failed: {error}"}
            )

    if prompt := st.chat_input("Describe your workflow..."):
        _on_chat_submit(prompt)
        st.rerun()


# ── Parameters panel ──────────────────────────────────────────────────────────

def _render_params_summary() -> None:
    params = st.session_state.params
    if not params:
        return

    def _val(key: str, fallback: str = "—") -> str:
        v = params.get(key)
        return str(v) if v not in (None, "", []) else fallback

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

    # Docking box — params["center"] is a list from LLM; widget keys are fallback
    def _coord(param_key: str, widget_keys: list) -> str:
        v = params.get(param_key)
        if isinstance(v, list) and len(v) == 3:
            return " / ".join(str(x) for x in v)
        parts = [st.session_state.get(k, "").strip() for k in widget_keys]
        return " / ".join(p if p else "—" for p in parts)

    center = _coord("center", ["param_center_x", "param_center_y", "param_center_z"])
    box = _coord("box_size", ["param_box_x", "param_box_y", "param_box_z"])

    with st.expander("Current parameters", expanded=True):
        st.markdown(
            f"**Protein:** {protein}  \n"
            f"**Ligands:** {ligands}  \n"
            f"**Center:** {center}  \n"
            f"**Box:** {box}"
        )


def render_parameters_panel() -> None:
    st.subheader("Input & Parameters")

    _render_params_summary()

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
    _render_docking_box()

    with st.expander("Advanced"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.text_input("pH", key="param_ph")
        with col2:
            st.text_input("Exhaustiveness", key="param_exhaustiveness")
        with col3:
            st.text_input("CPUs", key="param_cpu")

    st.divider()
    col_run, col_reset = st.columns(2)
    with col_run:
        if st.button("Run Docking", type="primary", use_container_width=True):
            _handle_run_docking()
    with col_reset:
        if st.button("Reset", use_container_width=True):
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
        path = _save_upload(uploaded, UPLOAD_DIR / uploaded.name)
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
        paths = [str(_save_upload(f, UPLOAD_DIR / f.name)) for f in uploaded_list]
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
        paths = [str(_save_upload(f, UPLOAD_DIR / f.name)) for f in uploaded_list]
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
    tab_job, tab_compare = st.tabs(["Job Viewer", "Compare Jobs"])

    with tab_job:
        _render_job_results()

    with tab_compare:
        comparison = st.session_state.get("comparison")
        if comparison:
            _render_comparison_table(comparison)
        else:
            st.caption("Select 2+ completed jobs in the sidebar and click Compare.")


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

    current_ligand = progress.get("current_ligand")
    if current_ligand:
        started_at = progress.get("current_started_at")
        elapsed_str = ""
        if started_at:
            try:
                started = datetime.fromisoformat(started_at)
                elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
                elapsed_str = f" (running {elapsed}s)"
            except ValueError:
                pass
        st.caption(f"Currently docking: `{current_ligand}`{elapsed_str}")


def _render_workflow_failure(job_id: str, result: dict) -> None:
    st.error(f"Job `{job_id}` failed.")
    for err in result.get("errors", []):
        st.caption(str(err))


def _render_job_results() -> None:
    bridge: OrchestratorBridge = st.session_state.bridge
    active_job_id = st.session_state.job_id
    viewed_job_id = st.session_state.get("viewed_job_id") or active_job_id

    if not viewed_job_id:
        st.caption(
            "No job selected yet. Configure parameters and click Run Docking, "
            "or click Load next to a job in the sidebar's Job History."
        )
        return

    # The job currently tracked by the live bridge takes priority — it may
    # still be running, in which case there's nothing on disk to fall back to.
    if bridge and viewed_job_id == active_job_id:
        if bridge.is_running():
            st.info(f"Job `{viewed_job_id}` is running...")
            _render_docking_progress(viewed_job_id)
            return

        result = bridge.get_result()
        error = bridge.get_error()

        if error:
            st.error(f"Job failed: {error}")
            return

        if result and result.get("status") == "completed":
            st.success(f"Job `{viewed_job_id}` completed")
            _render_affinity_chart(result)
            _render_visualization(result, viewed_job_id)
            _render_file_downloads(viewed_job_id)
            return

        if result and result.get("status") == "failed":
            _render_workflow_failure(viewed_job_id, result)
            return

    # Otherwise, view a completed job by reading its results straight from disk
    # — covers jobs Loaded from history and jobs with no live bridge.
    result = _load_workflow_results(viewed_job_id)
    if result and result.get("status") == "completed":
        st.info(f"Viewing job `{viewed_job_id}`")
        _render_affinity_chart(result)
        _render_visualization(result, viewed_job_id)
        _render_file_downloads(viewed_job_id)
        return

    if result and result.get("status") == "failed":
        _render_workflow_failure(viewed_job_id, result)
        return

    st.caption("No results found for this job.")


def _render_affinity_chart(result: dict) -> None:
    dock_results = result.get("results", {}).get("dock", {}).get("results", [])
    fig = make_affinity_chart_figure(dock_results)
    if fig:
        st.plotly_chart(fig, use_container_width=True)


def _load_job_in_pymol(job_id: str | None) -> tuple[bool, str]:
    """Ask the pymol-interactive helper API to load job structures.

    Returns (success, message) for display in the PyMOL tab.
    """
    if not job_id:
        return False, "No job selected."
    already_loaded = st.session_state.get("_pymol_loaded_job")
    if already_loaded == job_id:
        return True, f"Loaded job `{job_id}`."
    try:
        import requests as _req
        api_url = os.getenv("PYMOL_INTERACTIVE_URL", "http://pymol-interactive:8090")
        resp = _req.post(f"{api_url}/api/load_job", json={"job_id": job_id}, timeout=10)
        if resp.ok:
            st.session_state["_pymol_loaded_job"] = job_id
            return True, f"Loaded job `{job_id}` into PyMOL."
        return False, f"load_job failed ({resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return False, f"Cannot reach PyMOL helper API: {exc}"


def _resolve_container_path(path: str) -> Path:
    """Remap /app/orchestrator_output/... → OUTPUT_BASE/... so the UI container
    can access files written by MCP services that mount the volume at /app."""
    p = Path(path)
    try:
        # e.g. /app/orchestrator_output/job_x/... → job_x/...
        rel = p.relative_to("/app/orchestrator_output")
        return OUTPUT_BASE / rel
    except ValueError:
        return p


def _render_visualization(result: dict, job_id: str) -> None:
    raw_path = result.get("results", {}).get("visualize", {}).get("image_path")
    if not raw_path:
        return
    png_path = _resolve_container_path(raw_path)
    if not png_path.exists():
        return

    tab_img, tab_pymol = st.tabs(["Image", "PyMOL"])
    with tab_img:
        st.image(str(png_path))
        with open(png_path, "rb") as f:
            st.download_button("Download PNG", f, file_name="docking.png", mime="image/png")
    with tab_pymol:
        ok, msg = _load_job_in_pymol(job_id)
        if ok:
            st.success(msg, icon="✅")
        else:
            st.error(msg)
        novnc_url = os.getenv("PYMOL_NOVNC_URL", "http://localhost:6080") + "/vnc_auto.html"
        st.components.v1.iframe(novnc_url, height=600, scrolling=False)


def _render_file_downloads(job_id: str) -> None:
    if not job_id:
        return
    job_dir = OUTPUT_BASE / job_id
    if not job_dir.exists():
        return
    st.subheader("Files")
    for f in sorted(job_dir.rglob("*")):
        if f.is_file() and f.suffix in {".pdbqt", ".pml", ".json", ".png"}:
            with open(f, "rb") as fh:
                st.download_button(
                    f"Download {f.name}",
                    fh,
                    file_name=f.name,
                    key=f"dl_{f}",
                )


def _render_comparison_table(comparison: dict) -> None:
    st.markdown(build_comparison_html(comparison), unsafe_allow_html=True)
    st.caption("Green = strongest predicted binding (most negative kcal/mol). N/A = not docked in that job.")


# ── Layout ────────────────────────────────────────────────────────────────────

render_sidebar()

chat_col, params_col, results_col = st.columns([35, 25, 40])

with chat_col:
    render_chat()

with params_col:
    render_parameters_panel()

with results_col:
    render_results_panel()

# Keep polling while a job is running — 2 s delay avoids spinning the CPU.
if st.session_state.bridge and st.session_state.bridge.is_running():
    import time
    time.sleep(2)
    st.rerun()
