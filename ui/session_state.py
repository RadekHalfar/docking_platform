"""Session state initialization and parameter helpers."""
import streamlit as st

from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM, normalize_docking_algorithm
from services.llm_orchestrator import create_llm_client
from services.llm_orchestrator.interactive_session import InteractiveSession
from services.llm_orchestrator.workflow_parser import WorkflowParser
from ui.settings import load_settings, save_settings
from ui.worker_cap import default_worker_count, detect_moldina_worker_cap, max_workers_for

# Detected once at import time — the docking service's worker ceiling (and
# where it came from), used to cap the "max parallel workers" control, to
# default it to min(measured best, cap) and to explain both in the GUI.
WORKER_CAP = detect_moldina_worker_cap()

# Widget keys that mirror parsed parameters — set before widgets render to pre-fill them.
_WIDGET_DEFAULTS = {
    "param_job_name": "",
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
    "param_max_workers": default_worker_count(WORKER_CAP),
    "param_docking_algorithm": DEFAULT_DOCKING_ALGORITHM,
}

# Keyed widgets whose values must survive a run that stops (st.rerun())
# before rendering them -- see init(). Not the file uploaders (Streamlit
# forbids setting them via session state).
_PERSISTED_WIDGET_KEYS = (
    *_WIDGET_DEFAULTS,
    "input_mode", "revision_to_restore", "save_bundle_name", "save_scope",
)

# Shared by init() (first load) and start_new_session() (detaching from a
# job without a full page reload) so the greeting text lives in one place.
_GREETING_MESSAGE = {
    "role": "assistant",
    "content": (
        "Hello! Describe your docking workflow or use the form on the right.\n\n"
        "Example: *Download 6lu7 and dock ATP with center 10 20 30 box 25 30 35*"
    ),
}


def init() -> None:
    """Initialize all session state keys if not already set. Call once at app startup."""
    if "messages" not in st.session_state:
        st.session_state.messages = [dict(_GREETING_MESSAGE)]

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

    if "interactive_session" not in st.session_state:
        st.session_state.interactive_session = InteractiveSession()

    for key, default in _WIDGET_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default

    # Streamlit drops a keyed widget's value at the end of any run that
    # didn't render it -- a widget in a branch not taken this run (another
    # input mode), or one after a st.rerun() that cut the run short (a
    # sidebar Load/Delete runs before every panel; Run Docking before the
    # results panel's save fields).
    # Originally found when the chat column's reply reruns (gone since Stage
    # 13.4) wiped every parameter field. A value (re)assigned through
    # st.session_state during a run is exempt from that run's cleanup, so
    # re-assign them first thing every run.
    for key in _PERSISTED_WIDGET_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


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


def clear_box_picker_state() -> None:
    """Discard the box picker's loaded receptor and click marker.

    Used whenever that state might no longer correspond to the protein
    actually in play: starting a fresh box-picker load, the user
    discarding it explicitly, and -- the case Review Stage R1 (Phase 12)
    found missing -- detaching from or starting a new docking session,
    where a previously-loaded receptor could otherwise be silently reused
    for the wrong protein by a geometry chat command
    ("set up box center as protein center" / "look across all protein",
    via ``ui/app.py``'s ``_resolve_prepared_protein_pdb_path()``). Also
    drops a pending geometry command awaiting a fetch/prepare job started
    on its behalf (``ui/app.py``'s ``_handle_protein_geometry_command()``),
    for the same reason -- it shouldn't fire against a different job.
    """
    st.session_state.pop("_box_picker_receptor_pdb", None)
    st.session_state.pop("_box_picker_last_click", None)
    st.session_state.pop("_pending_geometry_target", None)
    st.session_state.pop("_box_picker_error", None)


def start_new_session() -> None:
    """Detach from the current job/session while keeping parameter widgets
    and LLM settings untouched.

    Clears job_id/viewed_job_id/bridge/interactive_session/chat so the next
    "Run Docking" click starts a brand-new job_dir/revision history, without
    losing whatever the user has already typed into the form. Used by the
    "New Session" button and by deleting the session currently active/in
    view (ui/app.py).
    """
    st.session_state.interactive_session = InteractiveSession()
    st.session_state.job_id = None
    st.session_state.job_name = ""
    st.session_state.viewed_job_id = None
    st.session_state.bridge = None
    st.session_state["_job_done"] = False
    st.session_state.pop("_pending_rerun", None)
    st.session_state.messages = [dict(_GREETING_MESSAGE)]
    clear_box_picker_state()


def get_params() -> dict:
    """Return the current workflow parameter dict."""
    return st.session_state.get("params", {})


def effective_cpu() -> int:
    """CPUs per ligand the form docks with: the "CPUs per ligand" field in
    Vina mode, always 1 for Moldina (PSO), which ignores --cpu -- a cpu left
    over from Vina mode would otherwise still cut its workers to
    quota // cpu on the server."""
    if st.session_state.get("param_docking_algorithm", DEFAULT_DOCKING_ALGORITHM) != "vina":
        return 1
    try:
        return max(1, int(st.session_state.get("param_cpu", "1")))
    except (TypeError, ValueError):
        return 1


def reset_workers_to_algorithm_default() -> None:
    """Set "Max parallel workers" to the selected algorithm's benchmarked
    best at the current CPUs per ligand. Called whenever the algorithm is
    switched (the selector's on_change, a chat command), never on a session
    load, whose saved worker count wins."""
    st.session_state["param_max_workers"] = default_worker_count(
        WORKER_CAP, st.session_state["param_docking_algorithm"], effective_cpu()
    )


def set_docking_algorithm_widget(algorithm: str) -> None:
    """Select *algorithm* in the GUI's selector, resetting the worker count
    to its benchmarked best if that is a real switch."""
    if st.session_state.get("param_docking_algorithm") == algorithm:
        return
    st.session_state["param_docking_algorithm"] = algorithm
    reset_workers_to_algorithm_default()


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

    # Callers pass docking_algorithm only when it was explicitly chosen
    # (a chat command, an LLM parse that named it): the validator's default
    # would otherwise reset the selector every message (Phase 15 finding 8).
    # An unrecognised name (the validator reports it as missing) is not
    # applied: a value outside the selector's options makes st.radio raise.
    algorithm = normalize_docking_algorithm(parsed_params.get("docking_algorithm"))
    if algorithm is not None:
        set_docking_algorithm_widget(algorithm)


def load_session_params(loaded_params: dict) -> None:
    """Replace current params with a loaded session's (or restored
    revision's) checkpointed parameters, syncing both
    st.session_state.params and the param_* widget pre-fill keys.

    Widgets are reset to their defaults first: apply_parsed_params() only
    writes keys present in *loaded_params*, so without the reset e.g. a
    stale param_protein_id would survive loading a protein_file revision.
    param_job_name is kept -- it only names the *next* new session.
    parallel/max_workers are applied here rather than in
    apply_parsed_params(), whose chat-parse callers carry the validator's
    default ``parallel: False`` and would reset the checkbox every message.
    A session without docking_algorithm (saved before Phase 15) reads as
    the default, Moldina; switching to its algorithm here doesn't reset the
    worker count -- the saved max_workers (or, without one, the default
    for that algorithm) applies.
    """
    for key, default in _WIDGET_DEFAULTS.items():
        if key != "param_job_name":
            st.session_state[key] = default
    st.session_state.params = {}
    apply_parsed_params(loaded_params)
    if loaded_params.get("parallel") is not None:
        st.session_state["param_parallel"] = bool(loaded_params["parallel"])
    if loaded_params.get("max_workers") is not None:
        # Clamped to the widget's own bounds (a checkpoint may come from a
        # machine with more cores than this one, or ran more CPUs per ligand).
        st.session_state["param_max_workers"] = min(
            max(1, int(loaded_params["max_workers"])), max_workers_for(WORKER_CAP, effective_cpu())
        )
