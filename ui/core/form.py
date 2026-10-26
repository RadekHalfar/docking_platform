"""The parameter form <-> workflow parameters (Phase 16, Stage 16.1).

``state.form`` holds
the form's field values under the ``param_*`` keys of
:func:`ui.core.state.form_defaults` (plus ``input_mode``); ``state.params``
holds the workflow parameters, including what the form doesn't show
(upload paths, chat-parsed values). The GUI keeps its widgets in step with
``state.form``.
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Optional

from common.docking_algorithm import (
    DEFAULT_DOCKING_ALGORITHM,
    cpus_per_ligand,
    docking_algorithm_label,
    normalize_docking_algorithm,
)
from common.utils import whole_number
from services.llm_orchestrator.interactive_session import InteractiveSession
from ui.core.state import GREETING_MESSAGE, GuiSession, reply
from ui.worker_cap import default_worker_count, max_workers_for

BOX_FORM_KEYS = (
    "param_center_x", "param_center_y", "param_center_z",
    "param_box_x", "param_box_y", "param_box_z",
)


def selected_docking_algorithm(state) -> str:
    """The algorithm the form's selector has chosen."""
    return state.form.get("param_docking_algorithm", DEFAULT_DOCKING_ALGORITHM)


def form_cpu(state) -> int:
    """The "CPUs per ligand" field as a whole number >= 1 (1 if unset).

    The form holds an int (the field's own values, and what
    :func:`apply_parsed_params` writes); the parse is a guard, since an
    unparsable value used to raise on every redraw (Review Stage R4).
    """
    return whole_number(state.form.get("param_cpu")) or 1


def effective_cpu(state) -> int:
    """CPUs per ligand the form docks with: :func:`form_cpu` in Vina mode,
    always 1 for Moldina (PSO), which ignores --cpu (:func:`cpus_per_ligand`,
    the same rule as the server's worker clamp). Sending 1 for PSO also
    keeps a cpu left over from Vina mode out of parameters.txt.
    """
    return cpus_per_ligand(selected_docking_algorithm(state), form_cpu(state))


def max_workers(state) -> int:
    """The "Max parallel workers" field's upper bound: the docking
    service's CPU quota // CPUs per ligand. A field value above it (e.g.
    after raising CPUs per ligand) is clamped to it here, as the server
    would clamp it anyway."""
    upper = max_workers_for(state.worker_cap, effective_cpu(state))
    if state.form.get("param_max_workers", 1) > upper:
        state.form["param_max_workers"] = upper
    return upper


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

    cpu = whole_number(parsed_params.get("cpu"))
    if cpu is not None:
        form["param_cpu"] = cpu

    # The chat callers pass only what the message named, never the
    # validator's defaults, so this moves the selector only on an explicit
    # choice (Phase 15 finding 8). An unrecognised name is not applied: the
    # selector has no option to show it.
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
            whole_number(loaded_params["max_workers"]) or 1,
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
    params["max_workers"] = whole_number(form.get("param_max_workers")) or 1

    return params


def sync_form_into_params(state) -> dict:
    """Fold the form into ``state.params`` (which also holds upload paths
    and chat-parsed values the form doesn't) and return a copy."""
    state.params.update(collect_form_params(state))
    return state.params.copy()


def clear_box_picker_state(state) -> None:
    """Discard the box picker's loaded receptor and its pending state.

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
    state.pending_geometry_targets = None
    state.box_picker_error = None


def detach_run(state) -> None:
    """Drop the current session's run: its bridge, its log and a staged
    redock. Shared by New Session and a Load, which each switch the active
    session (Review Stage R3: Load had missed the bridge)."""
    state.bridge = None
    state.run_log = []
    state.pending_rerun = None


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
    detach_run(state)
    state.job_done = False
    state.messages = [dict(GREETING_MESSAGE)]
    clear_box_picker_state(state)


def reset_session(state) -> None:
    """"Reset": everything back to a fresh session's values -- job, chat,
    parameters and the whole form -- except the LLM settings and client.

    The state object itself is kept (a GUI's views hold it); every field
    is replaced by a fresh :class:`GuiSession`'s. The caller must make sure
    no bridge is running (``run_control.active_bridge_job_id``)."""
    fresh = GuiSession(worker_cap=state.worker_cap, settings=state.settings, llm_client=state.llm_client)
    for f in fields(GuiSession):
        setattr(state, f.name, getattr(fresh, f.name))


def commit_protein_id(state) -> None:
    """The Protein ID field was committed (Enter / leaving the field): take
    it into ``state.params`` and confirm in chat, if it changed."""
    pid = str(state.form.get("param_protein_id", "")).strip()
    if pid and pid != state.params.get("protein_id", ""):
        state.params["protein_id"] = pid
        reply(state, f"Updated protein ID: **{pid}**")


def commit_ligand_ids(state) -> None:
    """The Ligand IDs field was committed: take the comma-separated IDs
    into ``state.params`` and confirm in chat, if they changed."""
    text = str(state.form.get("param_ligand_ids_text", "")).strip()
    ids = [l.strip() for l in text.split(",") if l.strip()]
    if ids and ids != state.params.get("ligand_ids"):
        state.params["ligand_ids"] = ids
        reply(state, f"Updated ligands: **{', '.join(ids)}**")


def params_summary(state) -> Optional[str]:
    """The "Current parameters" summary as markdown, or None while there
    is nothing to show (no parameters and empty Docking Box fields).

    The docking box and algorithm come from the form, not from
    ``params["center"]`` / ``params["box_size"]``: the form is what
    :func:`collect_form_params` docks with, and every writer updates it
    (typing, a box-picker click or size step, a chat parse, a geometry
    command), whereas the params lists only change on a chat parse or a
    run -- preferring them left the summary stale after a visual pick.
    """
    params = state.params
    form = state.form
    if not params and not any(str(form.get(k, "")).strip() for k in BOX_FORM_KEYS):
        return None

    if params.get("protein_id"):
        protein = params["protein_id"]
    elif params.get("protein_file"):
        protein = Path(params["protein_file"]).name
    else:
        protein = "—"

    if params.get("ligand_ids"):
        ligands = ", ".join(params["ligand_ids"])
    elif params.get("ligand_files"):
        # DEFERRED (Review Stage R4, docs/reviews/stage_16_r4.md, Deferred #2):
        # every file is listed, and the summary is redrawn on each Docking Box
        # keystroke -- about 30 KB of markdown per keystroke with 1000 files.
        # Fix if a large Batch Processing drop makes typing in the box fields
        # lag: list the first few and "(+N more)".
        ligands = ", ".join(Path(p).name for p in params["ligand_files"])
    else:
        ligands = "—"

    def coord(keys: tuple) -> str:
        parts = [str(form.get(k, "")).strip() for k in keys]
        return " / ".join(p if p else "—" for p in parts)

    return (
        f"**Protein:** {protein}  \n"
        f"**Ligands:** {ligands}  \n"
        f"**Center:** {coord(BOX_FORM_KEYS[:3])}  \n"
        f"**Box:** {coord(BOX_FORM_KEYS[3:])}  \n"
        f"**Algorithm:** {docking_algorithm_label(selected_docking_algorithm(state))}"
    )
