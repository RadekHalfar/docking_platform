"""The parameter form <-> workflow parameters (Phase 16, Stage 16.1).

Moved unchanged in behaviour from ``ui/session_state.py`` and ``ui/app.py``.
``state.form`` holds the form's field values under the ``param_*`` keys of
:func:`ui.core.state.form_defaults` (plus ``input_mode``); ``state.params``
holds the workflow parameters, including what the form doesn't show
(upload paths, chat-parsed values).

The Streamlit adapter's ``state.form`` is ``st.session_state`` itself, so
writing a form key here pre-fills the Streamlit widget of that key. Those
writes must happen before the widgets render (a callback, or code above
the panel), as before the extraction.
"""
from __future__ import annotations

from typing import Optional

from common.docking_algorithm import (
    DEFAULT_DOCKING_ALGORITHM,
    cpus_per_ligand,
    normalize_docking_algorithm,
)
from services.llm_orchestrator.interactive_session import InteractiveSession
from ui.core.state import GREETING_MESSAGE
from ui.worker_cap import default_worker_count, max_workers_for

BOX_FORM_KEYS = (
    "param_center_x", "param_center_y", "param_center_z",
    "param_box_x", "param_box_y", "param_box_z",
)


def selected_docking_algorithm(state) -> str:
    """The algorithm the form's selector has chosen."""
    return state.form.get("param_docking_algorithm", DEFAULT_DOCKING_ALGORITHM)


def effective_cpu(state) -> int:
    """CPUs per ligand the form docks with: the "CPUs per ligand" field in
    Vina mode, always 1 for Moldina (PSO), which ignores --cpu
    (:func:`cpus_per_ligand`, the same rule as the server's worker clamp).
    Sending 1 for PSO also keeps a cpu left over from Vina mode out of
    parameters.txt.

    NOTE (Review Stage R1, Phase 15): text that isn't a whole number
    ("four") silently counts as 1, the way the form's other numeric text
    fields (pH, Exhaustiveness) are read. A number input would remove the
    fallback; Phase 16 rebuilds this form in NiceGUI, so do it there
    (Stage 16.4), not twice.
    """
    try:
        cpu = int(state.form.get("param_cpu", "1"))
    except (TypeError, ValueError):
        cpu = 1
    return cpus_per_ligand(selected_docking_algorithm(state), cpu)


def reset_workers_to_algorithm_default(state) -> None:
    """Set "Max parallel workers" to the selected algorithm's benchmarked
    best at the current CPUs per ligand. Called whenever the algorithm is
    switched (the selector's change handler, a chat command), never on a
    session load, whose saved worker count wins."""
    state.form["param_max_workers"] = default_worker_count(
        state.worker_cap, selected_docking_algorithm(state), effective_cpu(state)
    )


def set_docking_algorithm(state, algorithm: str) -> None:
    """Select *algorithm* in the form, resetting the worker count to its
    benchmarked best if that is a real switch."""
    if state.form.get("param_docking_algorithm") == algorithm:
        return
    state.form["param_docking_algorithm"] = algorithm
    reset_workers_to_algorithm_default(state)


def apply_parsed_params(state, parsed_params: dict) -> None:
    """Sync LLM-extracted parameters into both ``state.params`` and the form.

    Writes every key it is given, so a chat caller passes only the keys the
    message named: a validator default would overwrite the user's own edit.
    """
    params = state.params
    for k, v in parsed_params.items():
        if v is not None:
            params[k] = v

    form = state.form
    if parsed_params.get("protein_id"):
        form["param_protein_id"] = parsed_params["protein_id"]

    if isinstance(parsed_params.get("ligand_ids"), list):
        form["param_ligand_ids_text"] = ", ".join(parsed_params["ligand_ids"])

    if parsed_params.get("ligand_folder"):
        form["param_ligand_folder_path"] = parsed_params["ligand_folder"]

    center = parsed_params.get("center")
    if isinstance(center, list) and len(center) == 3:
        form["param_center_x"] = str(center[0])
        form["param_center_y"] = str(center[1])
        form["param_center_z"] = str(center[2])

    box = parsed_params.get("box_size")
    if isinstance(box, list) and len(box) == 3:
        form["param_box_x"] = str(box[0])
        form["param_box_y"] = str(box[1])
        form["param_box_z"] = str(box[2])

    if parsed_params.get("ph") is not None:
        form["param_ph"] = str(parsed_params["ph"])

    if parsed_params.get("exhaustiveness") is not None:
        form["param_exhaustiveness"] = str(parsed_params["exhaustiveness"])

    if parsed_params.get("cpu") is not None:
        form["param_cpu"] = str(parsed_params["cpu"])

    # The chat callers pass only what the message named, never the
    # validator's defaults, so this moves the selector only on an explicit
    # choice (Phase 15 finding 8). An unrecognised name is not applied: a
    # value outside the selector's options makes st.radio raise.
    algorithm = normalize_docking_algorithm(parsed_params.get("docking_algorithm"))
    if algorithm is not None:
        set_docking_algorithm(state, algorithm)


def load_session_params(state, loaded_params: dict) -> None:
    """Replace current params with a loaded session's (or restored
    revision's) checkpointed parameters, syncing both ``state.params`` and
    the form.

    The form is reset to its defaults first: apply_parsed_params() only
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
    for key, default in state.form_defaults.items():
        if key != "param_job_name":
            state.form[key] = default
    state.params = {}
    apply_parsed_params(state, loaded_params)
    if loaded_params.get("parallel") is not None:
        state.form["param_parallel"] = bool(loaded_params["parallel"])
    if loaded_params.get("max_workers") is not None:
        # Clamped to the field's own bounds (a checkpoint may come from a
        # machine with more cores than this one, or ran more CPUs per ligand).
        state.form["param_max_workers"] = min(
            max(1, int(loaded_params["max_workers"])),
            max_workers_for(state.worker_cap, effective_cpu(state)),
        )


def read_box_fields(state) -> tuple[Optional[list[float]], Optional[list[float]]]:
    """``(center, box_size)`` from the Docking Box fields; each is None
    unless all three of its fields parse as numbers."""
    def triple(keys: tuple) -> Optional[list[float]]:
        try:
            return [float(state.form.get(k, "")) for k in keys]
        except (ValueError, TypeError):
            return None

    return triple(BOX_FORM_KEYS[:3]), triple(BOX_FORM_KEYS[3:])


def collect_form_params(state) -> dict:
    """Read the form and return a params dict for validation/execution."""
    form = state.form
    params: dict = {}
    mode = form.get("input_mode", "local_files")

    if mode == "rcsb":
        pid = form.get("param_protein_id", "").strip()
        if pid:
            params["protein_id"] = pid
        ligand_text = form.get("param_ligand_ids_text", "").strip()
        if ligand_text:
            params["ligand_ids"] = [l.strip() for l in ligand_text.split(",") if l.strip()]

    elif mode == "folder":
        pid = form.get("param_protein_id", "").strip()
        if pid:
            params["protein_id"] = pid
        # ligand_files are already in state.params, written on upload.

    # local_files: protein_file / ligand_files are already in state.params, written on upload.

    center, box_size = read_box_fields(state)
    if center is not None:
        params["center"] = center
    if box_size is not None:
        params["box_size"] = box_size

    for key, default, cast in [
        ("ph", "7.4", float),
        ("exhaustiveness", "8", int),
    ]:
        try:
            params[key] = cast(form.get(f"param_{key}", default))
        except (ValueError, TypeError):
            pass
    params["docking_algorithm"] = selected_docking_algorithm(state)
    params["cpu"] = effective_cpu(state)

    params["parallel"] = bool(form.get("param_parallel", False))
    try:
        params["max_workers"] = max(1, int(form.get("param_max_workers", 1)))
    except (ValueError, TypeError):
        params["max_workers"] = 1

    return params


def sync_form_into_params(state) -> dict:
    """Fold the form into ``state.params`` (which also holds upload paths
    and chat-parsed values the form doesn't) and return a copy."""
    state.params.update(collect_form_params(state))
    return state.params.copy()


def clear_box_picker_state(state) -> None:
    """Discard the box picker's loaded receptor and click marker.

    Used whenever that state might no longer correspond to the protein
    actually in play: starting a fresh box-picker load, the user
    discarding it explicitly, and -- the case Review Stage R1 (Phase 12)
    found missing -- detaching from or starting a new docking session,
    where a previously-loaded receptor could otherwise be silently reused
    for the wrong protein by a geometry chat command
    ("set up box center as protein center" / "look across all protein",
    via ``ui.core.box_picker.find_receptor_pdb()``). Also drops a pending
    geometry command awaiting a fetch/prepare job started on its behalf
    (``ui.core.chat.protein_geometry_command()``), for the same reason --
    it shouldn't fire against a different job.
    """
    state.box_picker_receptor_pdb = None
    state.box_picker_last_click = None
    state.pending_geometry_target = None
    state.box_picker_error = None


def start_new_session(state) -> None:
    """Detach from the current job/session while keeping the form and LLM
    settings untouched.

    Clears the job, bridge, interactive session and chat, so the next
    "Run Docking" starts a brand-new job_dir/revision history without
    losing whatever the user has already typed into the form. Used by the
    "New Session" button and by deleting the session currently active/in
    view.
    """
    state.interactive_session = InteractiveSession()
    state.job_id = None
    state.job_name = ""
    state.viewed_job_id = None
    state.bridge = None
    state.job_done = False
    state.pending_rerun = None
    state.messages = [dict(GREETING_MESSAGE)]
    clear_box_picker_state(state)
