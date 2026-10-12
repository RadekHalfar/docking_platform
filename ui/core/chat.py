"""The chat: deterministic commands and the LLM parameter parse (Phase 16,
Stage 16.2).

Moved unchanged in behaviour from ``ui/app.py``'s ``_on_chat_submit``.
:func:`submit` posts the user's message and answers it if it is a command
(geometry, algorithm, ``show history`` / ``undo``, ``save ... as``);
otherwise :func:`answer_with_llm` parses it.

The LLM path is split so a GUI can run only the slow part off its event
loop: :func:`parse_message` makes the LLM call and touches no state;
:func:`apply_parsed_message` then applies the result to the state, unless
the session changed meanwhile (:func:`parse_context`). The parse functions
are passed in, like the bridge class in ``run_control``, so each GUI's
tests can swap them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from common.docking_algorithm import docking_algorithm_label
from services.llm_orchestrator.workflow_llm_parser import (
    _deterministic_intent,
    detect_docking_algorithm_command,
    detect_protein_geometry_command,
    parse_followup_with_llm,
    parse_workflow_with_llm,
)
from ui.core import box_picker, save_results, sessions
from ui.core.form import apply_parsed_params, selected_docking_algorithm
from ui.core.state import reply

# Starts the box picker's receptor prep: () -> (started, message, severity).
StartPrep = Callable[[], tuple]

STALE_PARSE_REPLY = (
    "The session changed while I was reading that message, so I didn't "
    "apply it — please send it again."
)


def submit(state, message: str, output_base: Path, bridge_factory: Callable) -> bool:
    """Post the user's *message* and answer it if it is a deterministic
    command. Returns False if it is not one -- the caller then passes it
    to :func:`answer_with_llm`."""
    state.messages.append({"role": "user", "content": message})

    geometry_target = detect_protein_geometry_command(message)
    if geometry_target is not None:
        protein_geometry_command(
            state, geometry_target, output_base,
            lambda: box_picker.start_box_picker_prep(state, output_base, bridge_factory),
        )
        return True

    algorithm_command = detect_docking_algorithm_command(message)
    if algorithm_command is not None:
        docking_algorithm_command(state, algorithm_command["algorithm"])
        return True

    # Only the revision and save commands are taken from the CLI's
    # deterministic intents; everything else keeps the parameter-parsing
    # path (answer_with_llm).
    intent = _deterministic_intent(message)
    if intent is not None and intent["intent"] in ("show_history", "undo_revision"):
        reply(state, sessions.history_chat_reply(state, intent))
        return True
    if intent is not None and intent["intent"] == "save_results":
        reply(state, save_results.save_chat_reply(state, output_base, intent))
        return True
    return False


def protein_geometry_command(state, target: str, output_base: Path, start_prep: StartPrep) -> None:
    """Compute center/box_size from the loaded protein's bounding box and
    apply it, in response to a "set box center as protein center" / "look
    across all protein" chat command -- see
    ``detect_protein_geometry_command()``.

    If no protein has been fetched/prepared yet but the form already has
    enough to do that (a protein source is set), this kicks off the same
    background get_input_files+prepare_protein job "Load receptor for box
    picking" uses, via *start_prep* (normally
    :func:`ui.core.box_picker.start_box_picker_prep`), and queues *target*
    in ``state.pending_geometry_targets`` --
    :func:`ui.core.box_picker.process_box_picker_bridge` applies them once
    that job completes -- instead of failing outright. This is what makes
    the geometry commands usable on a first message, before any button has
    been clicked.
    """
    prep = state.box_picker_bridge
    if prep is not None and prep.is_running():
        # The receptor is on its way (the button, or an earlier command):
        # wait for it rather than start a second prep job, which would
        # replace this bridge and orphan its result. Checked before
        # find_receptor_pdb(): starting the prep cleared the picker's
        # receptor, so that would fall back to the session's last prepared
        # protein, which may not be the one being prepared now.
        _queue_geometry_target(state, target)
        reply(state, "I'm still preparing the protein — I'll set this once it's ready.")
        return

    receptor_path = box_picker.find_receptor_pdb(state, output_base)
    if receptor_path is not None:
        box_picker.apply_protein_geometry(state, receptor_path, target)
        return

    started, message, _severity = start_prep()
    if started:
        _queue_geometry_target(state, target)
        reply(
            state,
            "I don't have the protein ready yet — fetching and "
            "preparing it now, then I'll set this.",
        )
    else:
        reply(state, message)


def _queue_geometry_target(state, target: str) -> None:
    """Add *target* to the commands waiting for the receptor, in order and
    once each: "center" then "whole protein" sets both, not just the last."""
    queued = state.pending_geometry_targets or ()
    state.pending_geometry_targets = tuple(dict.fromkeys((*queued, target)))


def docking_algorithm_command(state, algorithm: str) -> None:
    """Apply a "use vina" / "dock with moldina" chat command. The GUI only
    switches -- docking always starts from Run Docking, so the CLI's
    "dock with ..." redock is not mirrored here."""
    apply_parsed_params(state, {"docking_algorithm": algorithm})
    label = docking_algorithm_label(algorithm)
    if state.interactive_session.has_active_job():
        hint = "Click **Run Docking** to redock with it (docking and visualization only)."
    else:
        hint = "It will be used when you click **Run Docking**."
    reply(state, f"Docking algorithm set to **{label}**. {hint}")


def parse_message(
    llm_client,
    message: str,
    prior_workflow: dict | None,
    *,
    parse_workflow: Callable = parse_workflow_with_llm,
    parse_followup: Callable = parse_followup_with_llm,
) -> tuple[dict, set]:
    """The LLM call: parse *message* into ``(parsed, named)`` -- the
    workflow, and the parameter names this message itself gave.

    With a *prior_workflow* (the parser's last result) it is a follow-up:
    only the delta is asked for and merged onto it. Blocking; reads and
    writes no GUI state, so it may run on a worker thread. A GUI that does
    so takes :func:`parse_context` before the call and passes it to
    :func:`apply_parsed_message` after it.
    """
    if prior_workflow and prior_workflow.get("parameters"):
        delta = parse_followup(
            llm_client,
            message,
            current_parameters=prior_workflow["parameters"],
            current_services=prior_workflow.get("services", []),
        )
        services = list(dict.fromkeys(
            prior_workflow.get("services", []) + delta.get("services_add", [])
        ))
        parameters = {**prior_workflow["parameters"], **delta.get("parameters", {})}
        return {"services": services, "parameters": parameters}, set(delta.get("parameters", {}))
    parsed = parse_workflow(llm_client, message)
    return parsed, set(parsed.get("parameters") or {})


def parse_context(state) -> tuple[Any, Any]:
    """What a :func:`parse_message` result was built on: the parser's
    workflow (the follow-up's prior) and the active interactive session.

    A Load, ``undo``, Run Docking or New Session while the LLM call is in
    flight replaces one of the two (by identity), and the result must not
    be applied then: it was merged onto the *old* prior, and validating it
    would overwrite the new session's parser state and form (Review Stage
    R1, deferred #2).
    """
    return state.parser.parsed_workflow, state.interactive_session


def apply_parsed_message(state, parsed: dict, named: set, *, context: Optional[tuple] = None) -> None:
    """Validate a :func:`parse_message` result, apply the parameters the
    message named to the form, and post the "Got it!" reply.

    With *context* (:func:`parse_context` taken before the LLM call), a
    result whose session has changed since is not applied; the reply says
    so instead."""
    if context is not None and any(a is not b for a, b in zip(context, parse_context(state))):
        reply(state, STALE_PARSE_REPLY)
        return
    validated = state.parser.validate_workflow(parsed)
    # Only what this message named reaches the form. validate_workflow()
    # fills every default (and the prior turn's values ride along in a
    # follow-up merge): applying those would reset the user's own
    # edits -- algorithm, CPUs per ligand, Exhaustiveness, pH -- on
    # every message (Phase 15 finding 8; Review Stage R1).
    p = validated["parameters"]
    missing = validated.get("missing_required", [])
    unknown_algorithm = None
    if "docking_algorithm" in missing:
        # Unrecognised (the LLM's own word for it): the selector keeps
        # its choice, and the parser does too, or the bad name would
        # ride along into every later follow-up merge and be asked
        # about again on each one.
        unknown_algorithm = p["docking_algorithm"]
        p["docking_algorithm"] = selected_docking_algorithm(state)
        missing.remove("docking_algorithm")
        named.discard("docking_algorithm")
    apply_parsed_params(state, {k: v for k, v in p.items() if k in named})

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
    if "docking_algorithm" in named:
        lines.append(f"- Algorithm: **{docking_algorithm_label(p['docking_algorithm'])}**")
    if unknown_algorithm is not None:
        lines.append(
            f"- Docking algorithm '{unknown_algorithm}' is not recognised, so "
            f"{docking_algorithm_label(p['docking_algorithm'])} stays selected. "
            "Use Moldina (PSO) or Vina (Monte Carlo)."
        )
    if missing:
        lines.append(f"\nStill needed: **{', '.join(missing)}** — please fill them in.")

    reply(state, "\n".join(lines))


def answer_with_llm(
    state,
    message: str,
    *,
    parse_workflow: Callable = parse_workflow_with_llm,
    parse_followup: Callable = parse_followup_with_llm,
) -> None:
    """Parse a non-command chat message with the LLM and apply it; any
    failure becomes the reply. Blocks for the LLM call -- a GUI with an
    event loop runs :func:`parse_message` off it and then
    :func:`apply_parsed_message` instead."""
    try:
        parsed, named = parse_message(
            state.llm_client,
            message,
            state.parser.parsed_workflow,
            parse_workflow=parse_workflow,
            parse_followup=parse_followup,
        )
        apply_parsed_message(state, parsed, named)
    except Exception as exc:
        reply(state, f"Sorry, I couldn't parse that: {exc}")
