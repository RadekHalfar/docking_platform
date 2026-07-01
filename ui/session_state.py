"""Session state initialization and parameter helpers."""
import os

import streamlit as st

from services.llm_orchestrator import create_llm_client
from services.llm_orchestrator.workflow_parser import WorkflowParser

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
    "param_cpu": "4",
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

    if "llm_client" not in st.session_state:
        st.session_state.llm_client = create_llm_client(
            provider=os.getenv("LLM_PROVIDER", "ollama"),
            endpoint=os.getenv("LLM_ENDPOINT", "http://localhost:11434"),
            model=os.getenv("LLM_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
        )

    if "params" not in st.session_state:
        st.session_state.params = {}

    if "input_mode" not in st.session_state:
        st.session_state.input_mode = "local_files"

    if "bridge" not in st.session_state:
        st.session_state.bridge = None

    if "job_id" not in st.session_state:
        st.session_state.job_id = None

    if "comparison" not in st.session_state:
        st.session_state.comparison = None

    for key, default in _WIDGET_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


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
