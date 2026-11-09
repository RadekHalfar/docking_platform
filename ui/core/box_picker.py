"""The box picker and protein geometry.

The picker keeps no box
of its own: the Docking Box form fields (``form.BOX_FORM_KEYS``) are the
only box state, and a viewer click or a − / + step writes them. The
receptor it shows comes from a background get_input_files +
prepare_protein job (``state.box_picker_bridge``), which a geometry chat
command can also start (``ui.core.chat.protein_geometry_command``).

As in ``run_control``, the bridge class is passed in (*bridge_factory*).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from common.path_utils import from_container_path
from common.pdb_geometry import compute_protein_center_and_box
from common.utils import generate_job_id
from services.llm_orchestrator.interactive_session import resolve_prepared_protein_pdb_path
from ui.core.form import (
    BOX_FORM_KEYS,
    apply_parsed_params,
    clear_box_picker_state,
    read_box_fields,
    sync_form_into_params,
)
from ui.core.state import reply

# Smallest box edge (Å) the box picker's − buttons step down to.
PICKER_MIN_BOX_SIZE = 1.0
# Box edge (Å) the picker's viewer draws while the Box size fields are unset
# (the component's own fallback), and what a click or a size step fills in
# then -- so the box that was shown is the box that gets docked.
PICKER_DEFAULT_BOX_SIZE = 20.0

PREP_SERVICES = ["get_input_files", "prepare_protein"]


def drawable_box_size(state) -> Optional[list[float]]:
    """The Docking Box size if the picker's viewer can draw it -- all three
    fields parse and are positive -- else None. With None the viewer shows
    a ``PICKER_DEFAULT_BOX_SIZE`` placeholder, and a click or − / + writes
    that placeholder into the fields first, so the box shown is the box
    docked."""
    _, box_size = read_box_fields(state)
    return box_size if box_size is not None and min(box_size) > 0 else None


def viewer_box_size(state) -> list[float]:
    """The box size the picker's viewer draws: :func:`drawable_box_size`,
    or the ``PICKER_DEFAULT_BOX_SIZE`` placeholder."""
    return drawable_box_size(state) or [PICKER_DEFAULT_BOX_SIZE] * 3


def apply_viewer_click(state, center: list) -> None:
    """Write a center clicked in the box picker's viewer into the Docking
    Box fields -- the picker keeps no box of its own, so "Run Docking" and
    the viewer both read these. If the viewer was showing its placeholder
    (``drawable_box_size()`` is None), fill that in, so the box picked is
    the box docked.
    """
    for key, value in zip(BOX_FORM_KEYS[:3], center):
        state.form[key] = f"{value:.3f}"
    if drawable_box_size(state) is None:
        for key in BOX_FORM_KEYS[3:]:
            state.form[key] = f"{PICKER_DEFAULT_BOX_SIZE:.3f}"


def step_box_size(state, axis: int, delta: float) -> None:
    """The picker's − / + buttons: change one Box size field by *delta* Å,
    never below ``PICKER_MIN_BOX_SIZE``. While the viewer shows its
    placeholder (``drawable_box_size()`` is None) the step starts from
    that placeholder and writes all three fields -- stepping one axis alone
    would leave a size the viewer still can't draw, so the button would
    seem to do nothing.
    """
    size = drawable_box_size(state)
    axes = (axis,) if size else range(3)
    size = size or [PICKER_DEFAULT_BOX_SIZE] * 3
    size[axis] = max(PICKER_MIN_BOX_SIZE, size[axis] + delta)
    for a in axes:
        state.form[BOX_FORM_KEYS[3 + a]] = f"{size[a]:.3f}"


def start_box_picker_prep(state, output_base: Path, bridge_factory: Callable) -> tuple[bool, Optional[str], str]:
    """Validate params and start the background get_input_files +
    prepare_protein job that fetches/prepares a receptor for box picking.

    Returns ``(started, message, severity)``. On success ``started`` is
    True and ``message`` is None. On failure ``started`` is False and
    ``message``/``severity`` ("error" or "warning") describe why, for the
    caller to surface however fits its UI (an inline error/warning for the
    button, a chat reply for the geometry auto-fetch).
    """
    params = sync_form_into_params(state)

    try:
        # A copy: validate_workflow() keeps the list in this client's parser,
        # and the module-level one is shared by every client.
        validated = state.parser.validate_workflow({"services": list(PREP_SERVICES), "parameters": params})
    except Exception as exc:
        return False, f"Validation error: {exc}", "error"

    missing = validated.get("missing_required", [])
    if missing:
        return False, f"Provide a protein source first — missing: {', '.join(missing)}", "warning"

    job_id = generate_job_id()
    clear_box_picker_state(state)

    bridge = bridge_factory()
    bridge.start_workflow(validated, job_id, str(output_base), services=list(PREP_SERVICES))
    state.box_picker_bridge = bridge
    return True, None, ""


def find_receptor_pdb(state, output_base: Path) -> Optional[Path]:
    """Find the prepared receptor PDB to compute protein geometry from.

    Prefers the box picker's already-loaded receptor (freshest, if the user
    used that control), else falls back to the active session's last
    completed ``prepare_protein`` result -- the same two sources the box
    picker and results viewer already read from. Returns ``None`` if
    neither is available or the file is no longer on disk.
    """
    raw_path = state.box_picker_receptor_pdb
    if not raw_path:
        raw_path = resolve_prepared_protein_pdb_path(state.interactive_session.last_results)

    if not raw_path:
        return None
    resolved = from_container_path(raw_path, output_base)
    return resolved if resolved.exists() else None


def read_protein_geometry(receptor_path: Path) -> tuple[list[float], list[float]]:
    """``(center, box_size)`` from *receptor_path*'s bounding box.

    The slow half of :func:`apply_protein_geometry` (reading and parsing a
    receptor of up to a few MB: 35-80 ms for 0.4-0.9 MB on a Docker Desktop
    bind mount), touching no state, so NiceGUI runs it off
    the event loop. Raises OSError / ValueError."""
    pdb_text = Path(receptor_path).read_bytes().decode("utf-8", errors="replace")
    return compute_protein_center_and_box(pdb_text)


def set_protein_geometry(state, target: str, center: list[float], box_size: list[float]) -> None:
    """Apply a :func:`read_protein_geometry` result to the Docking Box
    fields (which the box picker's viewer draws) and report it in chat."""
    if target == "center":
        apply_parsed_params(state, {"center": center})
        message = f"Protein center set to ({center[0]}, {center[1]}, {center[2]})."
    else:
        apply_parsed_params(state, {"box_size": box_size})
        message = (
            f"Box size set to ({box_size[0]}, {box_size[1]}, {box_size[2]}) "
            "to cover the whole protein (+10% margin)."
        )
    reply(state, message)


def geometry_failed(state, exc: Exception) -> None:
    reply(state, f"Could not compute protein geometry: {exc}")


def apply_protein_geometry(state, receptor_path: Path, target: str) -> None:
    """Compute center/box_size from *receptor_path*'s bounding box, apply it
    to the Docking Box fields, and report the result in chat -- all at once
    (the GUI passes its own *apply_geometry*, which reads off the event
    loop).

    The default *apply_geometry* of the immediate path
    (:func:`ui.core.chat.protein_geometry_command`, a receptor is already
    prepared) and the deferred one (:func:`process_box_picker_bridge`, once
    a just-started fetch/prepare job finishes).
    """
    try:
        center, box_size = read_protein_geometry(receptor_path)
    except (OSError, ValueError) as exc:
        geometry_failed(state, exc)
        return
    set_protein_geometry(state, target, center, box_size)


# How a caller applies protein geometry: (state, receptor_path, target).
ApplyGeometry = Callable[[object, Path, str], None]


def process_box_picker_bridge(
    state, output_base: Path, *, apply_geometry: ApplyGeometry = apply_protein_geometry
) -> bool:
    """Handle a finished "Load receptor for box picking" job: record the
    receptor, apply a geometry chat command that was waiting for it, and
    clear the bridge. Called on every page update / poll tick, like
    ``run_control.process_docking_bridge``; returns whether it handled a
    finished job (the state changed)."""
    bridge = state.box_picker_bridge
    if not bridge or bridge.is_running():
        return False

    error = bridge.get_error()
    result = bridge.get_result()
    # A geometry chat command ("use protein center...") may have started
    # this very job (no receptor was loaded yet), and more may have queued
    # while it ran -- pop them now so each is resolved (or reported as
    # failed) exactly once, however this job turns out.
    pending_targets = state.pending_geometry_targets or ()
    state.pending_geometry_targets = None
    panel_error = None  # shown in the picker section
    failure = None  # chat reply for a pending geometry command
    if error:
        panel_error = f"Could not prepare receptor: {error}"
        failure = f"Could not fetch the protein: {error}"
    elif result and result.get("status") == "completed":
        pdb_path = resolve_prepared_protein_pdb_path(result)
        if pdb_path:
            state.box_picker_receptor_pdb = pdb_path
            for target in pending_targets:
                apply_geometry(state, from_container_path(pdb_path, output_base), target)
        else:
            panel_error = "Receptor was prepared but no PDB path was returned."
            failure = "Receptor was prepared but no PDB path was returned -- could not set this."
    elif result and result.get("status") == "failed":
        failure_detail = "; ".join(result.get("errors", [])) or "unknown error"
        panel_error = "Could not prepare receptor: " + failure_detail
        failure = f"Could not fetch the protein: {failure_detail}"
    # NOTE (known gap): any other ending -- "cancelled", or no result and no
    # error -- clears a pending geometry command without a chat reply,
    # though the user was told "I'll set this". Unreachable today: nothing
    # stops the prep job, so it never ends "cancelled", and a missing result
    # always comes with an error. Fix if a Stop is added for the prep
    # job: add an else branch that sets *failure* (e.g. "Receptor
    # preparation was stopped -- could not set this.") and test it.
    if panel_error:
        state.box_picker_error = panel_error
    if pending_targets and failure:
        reply(state, failure)
    state.box_picker_bridge = None
    return True
