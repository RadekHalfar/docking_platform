"""Streamlit adapter for the GUI state (Stage 16.1).

The GUI's logic lives in the framework-free ``ui/core`` package and works on
a :class:`ui.core.state.GuiSession`. Streamlit keeps that state in
``st.session_state`` under its long-standing keys; :func:`gui_session`
returns a view of it with ``GuiSession``'s attributes, so the ``ui/core``
functions run against it unchanged. The helpers below are the page's
existing entry points, now thin delegations to ``ui/core``.
"""
import streamlit as st

from services.llm_orchestrator.interactive_session import InteractiveSession
from services.llm_orchestrator.workflow_parser import WorkflowParser
from ui.core import form, llm_settings
from ui.core.state import DEFAULT_INPUT_MODE, GREETING_MESSAGE, form_defaults
from ui.settings import build_llm_client, load_settings
from ui.worker_cap import detect_moldina_worker_cap

# Detected once at import time — the docking service's worker ceiling (and
# where it came from), used to cap the "max parallel workers" control, to
# default it to min(measured best, cap) and to explain both in the GUI.
WORKER_CAP = detect_moldina_worker_cap()

# Widget keys that mirror parsed parameters — set before widgets render to pre-fill them.
_WIDGET_DEFAULTS = form_defaults(WORKER_CAP)

# Keyed widgets whose values must survive a run that stops (st.rerun())
# before rendering them -- see init(). Not the file uploaders (Streamlit
# forbids setting them via session state).
_PERSISTED_WIDGET_KEYS = (
    *_WIDGET_DEFAULTS,
    "input_mode", "revision_to_restore", "save_bundle_name", "save_scope",
)

# Shared by init() (first load) and start_new_session() (detaching from a
# job without a full page reload) so the greeting text lives in one place.
_GREETING_MESSAGE = GREETING_MESSAGE

# GuiSession field -> (st.session_state key, value while the key is absent,
# whether assigning None removes the key). Keys removed on None are the
# ones the page (and its tests) treat as "absent" rather than None.
_STATE_KEYS = {
    "messages": ("messages", None, False),
    "params": ("params", None, False),
    "settings": ("settings", None, False),
    "llm_client": ("llm_client", None, False),
    "parser": ("parser", None, False),
    "interactive_session": ("interactive_session", None, False),
    "bridge": ("bridge", None, False),
    "box_picker_bridge": ("box_picker_bridge", None, False),
    "job_id": ("job_id", None, False),
    "job_name": ("job_name", "", False),
    "viewed_job_id": ("viewed_job_id", None, False),
    "job_done": ("_job_done", False, False),
    "run_log": ("_run_log", None, False),
    "pending_rerun": ("_pending_rerun", None, True),
    "comparison": ("comparison", None, False),
    "save_conflict": ("_save_conflict", None, True),
    "box_picker_receptor_pdb": ("_box_picker_receptor_pdb", None, True),
    "box_picker_last_click": ("_box_picker_last_click", None, True),
    "box_picker_error": ("_box_picker_error", None, True),
    "box_picker_job_id": ("_box_picker_job_id", None, False),
    "pending_geometry_targets": ("_pending_geometry_targets", None, True),
}


class _StreamlitGuiSession:
    """``GuiSession``'s attributes, stored in ``st.session_state``.

    ``form`` is ``st.session_state`` itself (the form fields are the
    ``param_*`` widget keys). ``worker_cap`` and ``form_defaults`` are read
    from this module's globals at each access, so tests that patch
    ``WORKER_CAP`` / ``_WIDGET_DEFAULTS`` reach the ``ui/core`` code too.
    """

    __slots__ = ()

    @property
    def form(self):
        return st.session_state

    @property
    def worker_cap(self):
        return WORKER_CAP

    @property
    def form_defaults(self):
        return _WIDGET_DEFAULTS

    def __getattr__(self, name):
        try:
            key, default, _ = _STATE_KEYS[name]
        except KeyError:
            raise AttributeError(name) from None
        return st.session_state.get(key, default)

    def __setattr__(self, name, value):
        try:
            key, _, drop_on_none = _STATE_KEYS[name]
        except KeyError:
            raise AttributeError(name) from None
        if value is None and drop_on_none:
            st.session_state.pop(key, None)
        else:
            st.session_state[key] = value


_GUI_SESSION = _StreamlitGuiSession()


def gui_session() -> _StreamlitGuiSession:
    """This browser tab's state, for the ``ui/core`` functions. Stateless
    itself -- every access goes to the current ``st.session_state``."""
    return _GUI_SESSION


def init() -> None:
    """Initialize all session state keys if not already set. Call once at app startup."""
    if "messages" not in st.session_state:
        st.session_state.messages = [dict(_GREETING_MESSAGE)]

    if "parser" not in st.session_state:
        st.session_state.parser = WorkflowParser()

    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()

    if "llm_client" not in st.session_state:
        st.session_state.llm_client = build_llm_client(st.session_state.settings)

    if "params" not in st.session_state:
        st.session_state.params = {}

    if "input_mode" not in st.session_state:
        st.session_state.input_mode = DEFAULT_INPUT_MODE

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

    if "_run_log" not in st.session_state:
        st.session_state["_run_log"] = []

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


def apply_settings(new_settings: dict) -> None:
    """See :func:`ui.core.llm_settings.apply_settings`."""
    llm_settings.apply_settings(gui_session(), new_settings)


def clear_box_picker_state() -> None:
    """See :func:`ui.core.form.clear_box_picker_state`."""
    form.clear_box_picker_state(gui_session())


def start_new_session() -> None:
    """See :func:`ui.core.form.start_new_session`."""
    form.start_new_session(gui_session())


def get_params() -> dict:
    """Return the current workflow parameter dict."""
    return st.session_state.get("params", {})


def selected_docking_algorithm() -> str:
    """See :func:`ui.core.form.selected_docking_algorithm`."""
    return form.selected_docking_algorithm(gui_session())


def effective_cpu() -> int:
    """See :func:`ui.core.form.effective_cpu`."""
    return form.effective_cpu(gui_session())


def reset_workers_to_algorithm_default() -> None:
    """See :func:`ui.core.form.reset_workers_to_algorithm_default` (the
    selector's on_change)."""
    form.reset_workers_to_algorithm_default(gui_session())


def set_docking_algorithm_widget(algorithm: str) -> None:
    """See :func:`ui.core.form.set_docking_algorithm`."""
    form.set_docking_algorithm(gui_session(), algorithm)


def apply_parsed_params(parsed_params: dict) -> None:
    """See :func:`ui.core.form.apply_parsed_params`."""
    form.apply_parsed_params(gui_session(), parsed_params)


def load_session_params(loaded_params: dict) -> None:
    """See :func:`ui.core.form.load_session_params`."""
    form.load_session_params(gui_session(), loaded_params)
