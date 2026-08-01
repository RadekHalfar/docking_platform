"""Session state initialization and parameter helpers."""
import streamlit as st

from services.llm_orchestrator import create_llm_client
from services.llm_orchestrator.workflow_parser import WorkflowParser
from ui.settings import load_settings, save_settings
from ui.worker_cap import detect_moldina_worker_cap

# Detected once at import time — used to pre-fill/cap the "max parallel
# workers" control with a reasonable, machine-appropriate default.
DETECTED_CPU_COUNT = detect_moldina_worker_cap()

# Widget keys that mirror parsed parameters — set before widgets render to pre-fill them.
_WIDGET_DEFAULTS = {
    "param_protein_id": "",
    "param_ligand_ids_text": "",
    "param_ligand_folder_path": "",
    "param_center_x": "",
    "param_center_y": "",
    "param_center_z": "",
    "param_box_x": "",
    "param_box_y": "",
    "param_box_z": "",
    "param_ph": "7.4",
    "param_exhaustiveness": "8",
    "param_cpu": "1",
    "param_parallel": False,
    "param_max_workers": DETECTED_CPU_COUNT,
}


def init() -> None:
    """Initialize all session state keys if not already set. Call once at app startup."""
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Hello! Describe your docking workflow or use the form on the right.\n\n"
                    "Example: *Download 6lu7 and dock ATP with center 10 20 30 box 25 30 35*"
                ),
            }
        ]

    if "parser" not in st.session_state:
        st.session_state.parser = WorkflowParser()

    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()

    if "llm_client" not in st.session_state:
        st.session_state.llm_client = _build_llm_client(st.session_state.settings)

    if "params" not in st.session_state:
        st.session_state.params = {}

    if "input_mode" not in st.session_state:
        st.session_state.input_mode = "local_files"

    if "bridge" not in st.session_state:
        st.session_state.bridge = None

    if "job_id" not in st.session_state:
        st.session_state.job_id = None

    if "viewed_job_id" not in st.session_state:
        st.session_state.viewed_job_id = None

    if "comparison" not in st.session_state:
        st.session_state.comparison = None

    for key, default in _WIDGET_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _build_llm_client(settings: dict):
    return create_llm_client(
        provider=settings["provider"],
        endpoint=settings["endpoint"],
        model=settings["model"],
        api_key=settings.get("api_key"),
        base_url=settings.get("base_url"),
    )


def apply_settings(new_settings: dict) -> None:
    """Persist settings to disk and rebuild the LLM client for the current session.

    api_key is kept in session state for immediate use but never written to disk.
    """
    st.session_state.settings = new_settings
    save_settings(new_settings)
    st.session_state.llm_client = _build_llm_client(new_settings)


def get_params() -> dict:
    """Return the current workflow parameter dict."""
    return st.session_state.get("params", {})


def apply_parsed_params(parsed_params: dict) -> None:
    """Sync LLM-extracted parameters into both params dict and widget pre-fill keys."""
    params = st.session_state.params
    for k, v in parsed_params.items():
        if v is not None:
            params[k] = v

    if parsed_params.get("protein_id"):
        st.session_state["param_protein_id"] = parsed_params["protein_id"]

    if isinstance(parsed_params.get("ligand_ids"), list):
        st.session_state["param_ligand_ids_text"] = ", ".join(parsed_params["ligand_ids"])

    if parsed_params.get("ligand_folder"):
        st.session_state["param_ligand_folder_path"] = parsed_params["ligand_folder"]

    center = parsed_params.get("center")
    if isinstance(center, list) and len(center) == 3:
        st.session_state["param_center_x"] = str(center[0])
        st.session_state["param_center_y"] = str(center[1])
        st.session_state["param_center_z"] = str(center[2])

    box = parsed_params.get("box_size")
    if isinstance(box, list) and len(box) == 3:
        st.session_state["param_box_x"] = str(box[0])
        st.session_state["param_box_y"] = str(box[1])
        st.session_state["param_box_z"] = str(box[2])

    if parsed_params.get("ph") is not None:
        st.session_state["param_ph"] = str(parsed_params["ph"])

    if parsed_params.get("exhaustiveness") is not None:
        st.session_state["param_exhaustiveness"] = str(parsed_params["exhaustiveness"])

    if parsed_params.get("cpu") is not None:
        st.session_state["param_cpu"] = str(parsed_params["cpu"])
