"""
Provider-neutral workflow parsing using an LLM client interface.
"""
import json
import logging
import re
import requests
from typing import Any, Dict, List, Optional

from common.docking_algorithm import normalize_docking_algorithm

from .llm_base import BaseLLMClient
from .workflow_parser import WorkflowParser

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a molecular docking workflow parser. Extract structured information from user requests.

Extract the following:
1. Services needed (choose from: get_input_files, prepare_ligands, prepare_protein, dock, visualize)
   - Always include "get_input_files" if protein/ligand IDs or a ligand_folder are mentioned
   - Always include "prepare_ligands" and "prepare_protein" if preparing or docking
   - Always include "dock" if docking is requested
   - Include "visualize" if user mentions ANY of these keywords: visualize, visualization, visualise, visualisation, view, render, rendering, image, picture, photo, show results, create visualization, display, see results, generate image

2. Parameters:
   - protein_id (4-character PDB ID)
    - ligand_ids (list of ligand identifiers or ligand names - CRITICAL: If user specifies multiple copies of the same ligand, you MUST duplicate the ligand ID in the array!)
   - ligand_folder (path to a local directory of ligand files — ONLY set this when the user
     explicitly names a folder/directory of ligands, e.g. "all ligands in /data/ligands" or
     "every ligand file in ./my_ligands". Copy the path EXACTLY as the user wrote it, including
     "./" or "/" prefixes — do not normalize, expand, or guess it. When ligand_folder is set,
     do NOT also set ligand_ids — omit ligand_ids entirely.)
   - center (docking box center [x, y, z])
   - box_size (docking box dimensions [x, y, z])
   - ph (pH value, default 7.4)
   - exhaustiveness (docking thoroughness, default 8; only affects the "vina" algorithm)
   - cpu (number of CPUs, default 1; only affects the "vina" algorithm)
   - population (PSO population, default 500; only affects the "moldina" algorithm)
   - docking_algorithm ("moldina" = Moldina PSO, the default, or "vina" = AutoDock Vina Monte Carlo).
     ONLY set this when the user names an algorithm (vina, autodock vina, monte carlo, moldina, PSO);
     otherwise omit it.
   - Include ph, exhaustiveness, cpu and population ONLY when the user states a value for them;
     otherwise omit them (their defaults are filled in later).

CRITICAL RULE FOR LIGAND_IDS:
- If user says "two WWO ligands", you MUST output: ["WWO", "WWO"]
- If user says "three ATP molecules", you MUST output: ["ATP", "ATP", "ATP"]
- If user says "dock protein and two WWO ligands", you MUST output: ["WWO", "WWO"]
- The array must contain the EXACT number of copies specified by the user!

Correct Examples:
User: "Dock 6lu7 with ATP" -> ligand_ids: ["ATP"], services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock"]
User: "Dock protein with two WWO ligands" -> ligand_ids: ["WWO", "WWO"]
User: "Dock 5ogo and two WWO ligands" -> ligand_ids: ["WWO", "WWO"]
User: "Use three ATP molecules" -> ligand_ids: ["ATP", "ATP", "ATP"]
User: "Dock with ATP and two HEM" -> ligand_ids: ["ATP", "HEM", "HEM"]
User: "Dock and create visualization" -> services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock with ATP and render image" -> services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock and show results" -> services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock protein 6lu7 against all ligands in /data/ligands" -> ligand_folder: "/data/ligands" (no ligand_ids)
User: "Dock 6lu7 with every ligand file in ./my_ligands" -> ligand_folder: "./my_ligands" (no ligand_ids)
User: "Dock 6lu7 with ATP using vina" -> ligand_ids: ["ATP"], docking_algorithm: "vina"

Wrong Examples (DO NOT DO THIS):
User: "Dock with two WWO ligands" -> ligand_ids: ["WWO"] WRONG! Should be ["WWO", "WWO"]
User: "Use three ATP" -> ligand_ids: ["ATP"] WRONG! Should be ["ATP", "ATP", "ATP"]
User: "Dock against all ligands in /data/ligands" -> ligand_ids: ["all ligands in /data/ligands"] WRONG! Should be ligand_folder: "/data/ligands"

CRITICAL RULE FOR CENTER AND BOX_SIZE:
- NEVER guess, invent, or use a default value for "center" or "box_size".
- Only include them in "parameters" if the user EXPLICITLY stated the numeric values.
- If either is not explicitly stated: OMIT it from "parameters" and add it to "missing_required".

Example 1 — center and box_size both explicitly given by the user:
{
  "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
  "parameters": {
    "protein_id": "5ogo",
    "ligand_ids": ["WWO", "WWO"],
    "center": [16.0, 9.0, 12.0],
    "box_size": [24.0, 28.0, 32.0]
  },
  "missing_required": []
}

Example 2 — center given but box_size NOT given (must be listed as missing):
{
  "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
  "parameters": {
    "protein_id": "6lu7",
    "ligand_ids": ["ATP"],
    "center": [10.0, 20.0, 30.0]
  },
  "missing_required": ["box_size"]
}

Example 3 — box_size given but center NOT given (must be listed as missing; NEVER copy box_size numbers into center):
{
  "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock"],
  "parameters": {
    "protein_id": "6lu7",
    "ligand_ids": ["ATP"],
    "box_size": [1.0, 10.0, 1.0]
  },
  "missing_required": ["center"]
}

Omit "ligand_ids" and instead include "ligand_folder": "<path>" in "parameters" when the user
named a folder of ligands instead of individual ligand IDs.

Do not add explanations, only return the JSON."""


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from a string that may contain extra text."""
    if not raw:
        return None

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(raw[start:end])
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None


_VISUALIZATION_KEYWORDS = [
    'visualiz', 'visualis', 'render', 'image', 'picture',
    'photo', 'view', 'display', 'show', 'see results', 'create vis'
]


def _add_visualize_if_mentioned(user_input: str, services: List[str]) -> List[str]:
    """Append 'visualize' to *services* if user_input mentions a visualization keyword."""
    user_input_lower = user_input.lower()
    if any(keyword in user_input_lower for keyword in _VISUALIZATION_KEYWORDS):
        if 'visualize' not in services:
            logger.info("Adding 'visualize' service based on keyword detection in user input")
            services.append('visualize')
    return services


def parse_workflow_with_llm(llm_client: BaseLLMClient, user_input: str) -> Dict[str, Any]:
    """Parse natural language workflow request into structured format."""
    user_prompt = f"Parse this workflow request:\n\n{user_input}"
    response = llm_client.generate(user_prompt, system=SYSTEM_PROMPT, temperature=0.1)

    parsed = _extract_json_object(response)
    if parsed is None:
        logger.error(f"No JSON found in response: {response}")
        raise ValueError("No JSON found in LLM response")

    logger.info(f"LLM parsed services: {parsed.get('services', [])}")
    logger.info(f"LLM parsed ligand_ids: {parsed.get('parameters', {}).get('ligand_ids', [])}")

    parsed['services'] = _add_visualize_if_mentioned(user_input, parsed.get('services', []))

    return parsed


_FOLLOWUP_SYSTEM_PROMPT = """You are continuing to build a molecular docking workflow across multiple chat turns. \
The user already gave you some parameters in earlier turns; now they send one more message that adds to, \
changes, or clarifies the workflow. Extract ONLY what this new message changes.

Return strict JSON with this exact shape:
{
  "services_add": [],
  "parameters": {}
}

Rules:
1. "services_add" lists any NEW services this message implies (from: get_input_files, prepare_ligands,
   prepare_protein, dock, visualize) that were not already active. Return [] if none.
2. For scalar fields (protein_id, center, box_size, ph, exhaustiveness, cpu, population, docking_algorithm):
   include a field in "parameters" ONLY if this message explicitly gives or changes that value. If the message
   does not mention a field, OMIT it entirely — do not guess, repeat, or invent a value. Omitted fields keep
   their previous value; you don't need to restate it. docking_algorithm is "moldina" (Moldina PSO) or
   "vina" (AutoDock Vina Monte Carlo).
3. For ligand_ids: you are given the CURRENT ligand list already collected. Interpret this message against
   that current list and return the COMPLETE resulting array (not just the newly added/removed part):
   - "add"/"also"/"another"/"one more"/"plus" -> extend the current list with the new ligand(s), preserving
     the ligands already present.
   - "change"/"set"/"use"/"switch to"/"instead" (without "add") -> REPLACE the current list entirely with
     the new one.
   - "remove"/"drop"/"take out" -> remove the specified number of matching entries from the current list.
   - Preserve exact multiplicity: "two ATP" means two entries, "three HEM" means three entries.
   - If ligands are not mentioned at all in this message, OMIT "ligand_ids" from "parameters".
4. Never invent, guess, or default center/box_size — only include them if explicit numeric [x, y, z] values
   are stated in this message.
5. Do not add explanations, only return the JSON.

Examples:

Current ligand_ids: ["ATP"]
Message: "can you add one atp ligand?"
Output: {"services_add": [], "parameters": {"ligand_ids": ["ATP", "ATP"]}}

Current ligand_ids: ["ATP"]
Message: "also add two HEM"
Output: {"services_add": [], "parameters": {"ligand_ids": ["ATP", "HEM", "HEM"]}}

Current ligand_ids: ["ATP", "ATP"]
Message: "remove one ATP"
Output: {"services_add": [], "parameters": {"ligand_ids": ["ATP"]}}

Current ligand_ids: ["ATP"]
Message: "change ligand to HEM"
Output: {"services_add": [], "parameters": {"ligand_ids": ["HEM"]}}

Current ligand_ids: ["ATP"]
Message: "actually set pH to 6.5"
Output: {"services_add": [], "parameters": {"ph": 6.5}}

Current ligand_ids: ["ATP"]
Message: "and render an image of the result"
Output: {"services_add": ["visualize"], "parameters": {}}
"""


def parse_followup_with_llm(
    llm_client: BaseLLMClient,
    user_input: str,
    current_parameters: Dict[str, Any],
    current_services: List[str],
) -> Dict[str, Any]:
    """Extract what one follow-up chat message changes, given the workflow already
    collected so far. Returns ``{"services_add": [...], "parameters": {...}}``
    containing ONLY fields this message actually touches — callers merge the
    result onto the prior state instead of replacing it.
    """
    user_prompt = (
        f"Current ligand_ids: {current_parameters.get('ligand_ids', [])}\n"
        f"Current parameters: {current_parameters}\n"
        f"Current services: {current_services}\n\n"
        f"New message:\n{user_input}"
    )
    response = llm_client.generate(user_prompt, system=_FOLLOWUP_SYSTEM_PROMPT, temperature=0.1)

    parsed = _extract_json_object(response)
    if parsed is None:
        logger.error(f"No JSON found in follow-up response: {response}")
        raise ValueError("No JSON found in LLM response")

    parameters = parsed.get("parameters", {})
    if not isinstance(parameters, dict):
        parameters = {}

    services_add = parsed.get("services_add", [])
    if not isinstance(services_add, list):
        services_add = []
    services_add = _add_visualize_if_mentioned(user_input, services_add)

    logger.info(f"LLM follow-up services_add: {services_add}")
    logger.info(f"LLM follow-up parameters: {parameters}")

    return {"services_add": services_add, "parameters": parameters}


_FOLLOW_UP_QUESTION_SYSTEM = (
    "You are a concise assistant for molecular docking workflow clarification. "
    "Given current workflow state and one target parameter, produce ONE short question to ask the user. "
    "Do not provide explanations, lists, or JSON."
)

_EXTRACT_UPDATES_SYSTEM = (
    "You extract structured parameter updates for a molecular docking workflow from one user clarification turn. "
    'Return JSON only with this exact shape: {"parameter_updates": {...}}. '
    "Never include markdown, explanations, comments, or code fences. "
    'Do not invent values. If the answer is ambiguous, unrelated, or insufficient, return {"parameter_updates": {}}.'
)

_EXTRACT_BOX_SOURCE_SYSTEM = (
    "You classify one user answer for docking box input mode. "
    'Return JSON only with this exact shape: {"box_source": "manual"|"pymol"|"unknown"}. '
    "Never include markdown, explanations, comments, or code fences."
)


def generate_llm_follow_up_question(
    llm_client: BaseLLMClient,
    parser_obj: WorkflowParser,
    current_param: str,
) -> str:
    """Generate a concise follow-up question for one missing/invalid parameter.

    Falls back to a deterministic question string if the LLM call fails.
    """
    issue = parser_obj.get_parameter_issue(current_param)
    current_workflow = parser_obj.parsed_workflow or {"services": [], "parameters": {}, "missing_required": []}

    prompt = (
        f"Target parameter: {current_param}\n"
        f"Issue type: {issue or 'missing'}\n"
        f"Missing required: {current_workflow.get('missing_required', [])}\n"
        f"Current parameters: {current_workflow.get('parameters', {})}\n"
        "Ask one short question to resolve only the target parameter."
    )

    try:
        response = llm_client.generate(prompt, system=_FOLLOW_UP_QUESTION_SYSTEM, temperature=0.1)
        question = response.strip().splitlines()[0].strip()
        if question:
            return question
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning(f"Failed to generate LLM follow-up question for '{current_param}': {exc}")

    # Deterministic fallback when LLM call fails.
    if current_param == "protein_id":
        if issue == "invalid":
            return "The protein PDB ID looks invalid. Please provide a valid 4-character ID (e.g., 6lu7):"
        return "I am missing the protein PDB ID. Please provide it (e.g., 6lu7):"
    if current_param == "ligand_ids":
        return "I am missing ligand IDs or names. Please provide comma-separated values (e.g., ATP,HEM,aspirin):"
    if current_param == "center":
        return "Please provide docking box center as x,y,z (e.g., 10,20,30):"
    if current_param == "box_size":
        return "Please provide docking box size as x,y,z (e.g., 25,30,35):"
    return f"Please provide {current_param}:"


def extract_updates_with_llm(
    llm_client: BaseLLMClient,
    parser_obj: WorkflowParser,
    user_answer: str,
    target_param: str,
) -> Dict[str, Any]:
    """Use LLM to convert one user clarification reply into strict JSON parameter updates.

    Returns an empty dict when the LLM call fails or produces no usable value.
    """
    workflow = parser_obj.parsed_workflow or {"services": [], "parameters": {}, "missing_required": []}

    prompt = (
        "Task: read one user reply and convert it into workflow parameter updates.\n\n"
        f"Current services: {workflow.get('services', [])}\n"
        f"Current parameters: {workflow.get('parameters', {})}\n"
        f"Missing required: {workflow.get('missing_required', [])}\n"
        f"Target parameter: {target_param}\n"
        f"User answer: {user_answer}\n\n"
        "Rules:\n"
        "1. Update only values that are explicitly supported by the user answer.\n"
        "2. Prefer the target parameter. Do not modify unrelated fields unless the user answer clearly provides them too.\n"
        "3. Allowed fields only: protein_id, ligand_ids, center, box_size, ph, exhaustiveness, cpu, population, num_modes, energy_range, docking_algorithm.\n"
        "4. Never infer missing scientific values from defaults, prior examples, or typical practice.\n"
        '5. If the user answer is ambiguous, partial, contradictory, or not machine-usable, return {"parameter_updates": {}}.\n'
        "6. Keep existing workflow capabilities intact: do not invent new services or new parameter names.\n"
        "7. Deterministic UI choices like manual/default/PyMOL selection are handled elsewhere; only extract concrete parameter values from the answer.\n\n"
        "Field constraints:\n"
        '- protein_id: exactly one 4-character alphanumeric string, for example "6lu7".\n'
        "- ligand_ids: JSON array of strings. Preserve multiplicity exactly when the user specifies counts.\n"
        '- Count words must be expanded when explicit, e.g. one/two/three ATP -> ["ATP"], ["ATP", "ATP"], ["ATP", "ATP", "ATP"].\n'
        '- center and box_size: JSON array of exactly 3 numbers, extracted from forms like "10,20,30" or "[10, 20, 30]".\n'
        "- Numeric scalar fields must be returned as numbers, not strings.\n"
        '- docking_algorithm: exactly "moldina" (Moldina PSO) or "vina" (AutoDock Vina Monte Carlo).\n\n'
        "Examples:\n"
        "User answer: 6lu7\n"
        'Output: {"parameter_updates": {"protein_id": "6lu7"}}\n\n'
        "User answer: use ATP and two HEM ligands\n"
        'Output: {"parameter_updates": {"ligand_ids": ["ATP", "HEM", "HEM"]}}\n\n'
        "User answer: center should be 10, 20, 30\n"
        'Output: {"parameter_updates": {"center": [10.0, 20.0, 30.0]}}\n\n'
        "User answer: not sure\n"
        'Output: {"parameter_updates": {}}'
    )

    try:
        raw = llm_client.generate(prompt, system=_EXTRACT_UPDATES_SYSTEM, temperature=0.1)
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning(f"Failed to extract parameter updates with LLM: {exc}")
        return {}

    payload = _extract_json_object(raw)
    if not payload:
        logger.warning("LLM returned non-JSON output for parameter updates")
        return {}

    updates = payload.get("parameter_updates", {})
    if not isinstance(updates, dict):
        return {}

    return updates


def extract_box_source_choice_with_llm(
    llm_client: BaseLLMClient,
    user_answer: str,
) -> Optional[str]:
    """Classify whether user prefers manual coordinates or PyMOL box definition.

    Returns "manual", "pymol", or None when classification is unknown/unusable.
    """
    prompt = (
        "Classify this user answer for docking box source preference.\n\n"
        f"User answer: {user_answer}\n\n"
        "Rules:\n"
        "1. Return manual when user prefers typing coordinates or explicitly avoids PyMOL.\n"
        "2. Return pymol when user prefers defining/selecting the box in interactive PyMOL.\n"
        "3. Return unknown when answer is ambiguous, mixed, or not about this choice.\n"
        "4. Do not extract coordinates or other workflow parameters here.\n"
        "5. Do not guess.\n\n"
        "Examples:\n"
        "Answer: use pymol interactive -> {\"box_source\": \"pymol\"}\n"
        "Answer: I will type center and size manually -> {\"box_source\": \"manual\"}\n"
        "Answer: not sure -> {\"box_source\": \"unknown\"}"
    )

    try:
        raw = llm_client.generate(prompt, system=_EXTRACT_BOX_SOURCE_SYSTEM, temperature=0.1)
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning(f"Failed to classify docking box source with LLM: {exc}")
        return None

    payload = _extract_json_object(raw)
    if not payload:
        return None

    choice = payload.get("box_source")
    if not isinstance(choice, str):
        return None

    normalized = choice.strip().lower()
    if normalized in {"manual", "pymol"}:
        return normalized

    return None

_SESSION_INTENTS = frozenset({
    "new_workflow",
    "search_ligand",
    "update_parameters",
    "use_pymol_box",
    "set_protein_geometry",
    "set_docking_algorithm",
    "rerun_steps",
    "show_state",
    "show_history",
    "undo_revision",
    "save_results",
    "end_session",
    "help",
    "unknown",
})

_INTENT_CLASSIFIER_SYSTEM = (
    "You classify follow-up commands inside an active molecular docking session. "
    "The session already has a running job. Classify the user's intent to exactly one of these values:\n"
    "- new_workflow: user wants to start a completely new workflow or switch to a different protein/job\n"
    "- search_ligand: user wants to search ligand candidates by common name before choosing one\n"
    "- update_parameters: user wants to change one or more docking parameters (ligand, center, box size, etc.)\n"
    "- use_pymol_box: user wants to use interactive PyMOL to define the docking box\n"
    "- rerun_steps: user explicitly asks to rerun a docking or preparation step\n"
    "- show_state: user wants to inspect current active job parameters or status\n"
    "- show_history: user wants to see the list of saved revisions or parameter history\n"
    "- undo_revision: user wants to revert parameters/state to a prior revision\n"
    "- save_results: user wants to save/copy current docking or visualization outputs under a name\n"
    "- end_session: user wants to end or quit the session\n"
    "- help: user wants to see available commands\n"
    "- unknown: the intent does not match any of the above\n\n"
    'Return JSON only with this exact shape: {"intent": "<intent_name>", "name": null, "scope": "all"}. '
    "For save_results, set name to the save target name if the user provided one, else null. "
    "For search_ligand, set name to the ligand query string to search (for example 'aspirin'). "
    "For save_results, set scope to 'docking' when only docking results are requested, "
    "'visualization' when only visualization is requested, else 'all'. "
    "For undo_revision, set name to the revision number string if provided, else null. "
    "Never include markdown, explanations, or code fences."
)

_EXTRACT_PARAM_UPDATES_FROM_COMMAND_SYSTEM = (
    "You extract structured docking parameter updates from a natural language command. "
    'Return JSON only with this exact shape: {"parameter_updates": {...}}. '
    "Never include markdown, explanations, comments, or code fences. "
    'Do not invent values. If the command is ambiguous or unrelated, return {"parameter_updates": {}}. '
    "Allowed fields only: protein_id, ligand_ids, center, box_size, ph, exhaustiveness, cpu, population, num_modes, energy_range, docking_algorithm. "
    "ligand_ids must be a JSON array of strings. "
    'docking_algorithm must be exactly "moldina" (Moldina PSO) or "vina" (AutoDock Vina Monte Carlo). '
    "center and box_size must be JSON arrays of exactly 3 numbers. "
    "Preserve multiplicity when counts are stated: 'two ATP' → [\"ATP\",\"ATP\"], 'three HEM' → [\"HEM\",\"HEM\",\"HEM\"]. "
    "Examples:\n"
    "Command: change ligand to HEM\n"
    'Output: {"parameter_updates": {"ligand_ids": ["HEM"]}}\n\n'
    "Command: set center to 10, 20, 30\n"
    'Output: {"parameter_updates": {"center": [10.0, 20.0, 30.0]}}\n\n'
    "Command: use two ATP ligands\n"
    'Output: {"parameter_updates": {"ligand_ids": ["ATP", "ATP"]}}\n\n'
    "Command: set the docking algorithm to autodock vina\n"
    'Output: {"parameter_updates": {"docking_algorithm": "vina"}}\n\n'
    "Command: save results as hem_trial\n"
    'Output: {"parameter_updates": {}}'
)


_SAVE_PREFIXES = (
    "save results",
    "save docking",
    "save visualization",
    "save viz",
    "save as",
)


# "set box center as protein center" / "use protein center" / "center the
# box on the protein" -- deliberately requires the word "protein" near
# "center" rather than matching "center" alone, since "set center to
# 10,20,30" must keep going to update_parameters.
_PROTEIN_CENTER_RE = re.compile(
    r"\bprotein\s+center\b"
    r"|\bcenter\b(?:\s+\S+){0,4}\s+protein\b",
    re.IGNORECASE,
)

# "look across all protein" / "cover the whole protein" / "whole protein
# box" / "box size for the entire protein". Anchored to end-of-string
# (allowing only trailing "box"/punctuation) rather than a bare .search()
# match -- Review Stage R1 (Phase 12) found the unanchored version matched
# "cover the whole protein pocket"/"...active site", silently swapping a
# user's targeted box-resize request for a full-protein blind-docking box.
# Requiring "protein" to end the phrase (mod "box"/punctuation) keeps the
# intended cases matching while rejecting phrases that qualify "protein"
# with something more specific than the whole structure.
_WHOLE_PROTEIN_BOX_RE = re.compile(
    r"\b(?:look(?:s|ing)?\s+across|cover(?:s|ing)?|encompass(?:es|ing)?|span(?:s|ning)?)\s+"
    r"(?:the\s+|all\s+)?(?:whole\s+|entire\s+)?protein(?:\s+box)?[\s.!?]*$"
    r"|\b(?:whole|entire|all)\s+protein\s+box[\s.!?]*$"
    r"|\bbox\b.{0,40}?\b(?:whole|entire)\s+protein(?:\s+box)?[\s.!?]*$",
    re.IGNORECASE,
)


def detect_protein_geometry_command(text: str) -> Optional[str]:
    """Detect a chat command asking to auto-fill the docking box from the
    loaded protein's own bounding-box geometry (see
    :func:`common.pdb_geometry.compute_protein_center_and_box`).

    Pure regex, no LLM call -- deliberately deterministic, matching this
    codebase's existing rule that `center`/`box_size` are never guessed by
    the LLM. Returns ``"center"``, ``"box_size"``, or ``None`` (no match,
    caller should fall through to its normal parsing path).
    """
    if _PROTEIN_CENTER_RE.search(text):
        return "center"
    if _WHOLE_PROTEIN_BOX_RE.search(text):
        return "box_size"
    return None


# "use vina" / "switch to PSO" / "set docking_algorithm = vina" / "dock with
# vina". Anchored over the whole command (only "the", a trailing
# "algorithm"/"search"/"mode" and punctuation may surround the algorithm), so
# "dock 6lu7 with ATP" or "dock with vina and visualize" never match -- the
# former must stay a rerun, the latter a longer request (Phase 15 finding 6).
_ALGORITHM_TERM = (
    r"(?P<alg>autodock[\s_-]+vina"
    r"|vina(?:\s*\(\s*monte[\s_-]+carlo\s*\)|[\s_-]+monte[\s_-]+carlo)?"
    r"|monte[\s_-]+carlo"
    r"|moldina(?:\s*\(\s*pso\s*\)|[\s_-]+pso)?"
    r"|pso)"
    r"(?:\s+(?:algorithm|search|mode))?[\s.!]*$"
)
_ALGORITHM_KEY_SEP = r"(?:\s+(?:to|as)\s+|\s*[=:]\s*|\s+)(?:the\s+)?"
_DOCK_WITH_ALGORITHM_RE = re.compile(
    r"^(?:please\s+)?(?:re[\s-]?dock|dock|re[\s-]?run|run|execute)"
    r"(?:\s+(?:it|docking))?(?:\s+again)?\s+(?:with|using)\s+(?:the\s+)?" + _ALGORITHM_TERM,
    re.IGNORECASE,
)
_SET_ALGORITHM_RE = re.compile(
    r"^(?:please\s+)?(?:"
    r"(?:use|switch\s+to|change\s+to)\s+(?:the\s+)?"
    r"|(?:(?:set|change|switch|use)\s+(?:the\s+)?)?(?:docking[\s_]+)?algorithm" + _ALGORITHM_KEY_SEP +
    r")" + _ALGORITHM_TERM,
    re.IGNORECASE,
)


def detect_docking_algorithm_command(text: str) -> Optional[Dict[str, Any]]:
    """Detect a chat command that switches the docking algorithm.

    Pure regex, no LLM call, like :func:`detect_protein_geometry_command`.
    Returns ``{"algorithm": "moldina"|"vina", "rerun": bool}`` -- *rerun* is
    True for "dock/rerun/run with <algorithm>", which also asks to redock --
    or ``None`` (no match; the caller falls through to its normal parsing).
    An unrecognised name ("use gnina") is not a match, so it reaches the
    LLM parameter extraction, whose output is validated.
    """
    stripped = text.strip()
    rerun = True
    match = _DOCK_WITH_ALGORITHM_RE.match(stripped)
    if match is None:
        rerun = False
        match = _SET_ALGORITHM_RE.match(stripped)
    if match is None:
        return None
    algorithm = normalize_docking_algorithm(match.group("alg"))
    if algorithm is None:
        return None
    return {"algorithm": algorithm, "rerun": rerun}


def _deterministic_intent(text: str) -> Optional[Dict[str, Any]]:
    """Return an intent result dict if *text* matches an unambiguous pattern, else None."""
    t = text.strip().lower()

    if t in ("state", "status", "show state", "show status"):
        return {"intent": "show_state", "name": None}

    if t in ("history", "show history", "revisions", "list revisions", "log"):
        return {"intent": "show_history", "name": None}

    if t in ("end", "quit", "exit", "q", "end session", "quit session", "exit session"):
        return {"intent": "end_session", "name": None}

    if t in ("help", "h", "?"):
        return {"intent": "help", "name": None}

    if re.match(r"^undo\b", t):
        # Only a number right after "undo" is the revision: taking the first
        # digit anywhere made "undo the box change and set exhaustiveness to
        # 16" restore revision 16 (Review Stage R1, Phase 13). Without one,
        # name is None and the caller asks which revision.
        match = re.match(r"^undo\s+(?:to\s+)?(?:rev(?:ision)?\s*)?#?(\d+)\s*$", t)
        return {"intent": "undo_revision", "name": match.group(1) if match else None}

    _SAVE_SCOPE_MAP = {
        "save visualization": "visualization",
        "save viz": "visualization",
        "save docking": "docking",
    }
    save_scope = next((scope for prefix, scope in _SAVE_SCOPE_MAP.items() if t.startswith(prefix)), None)
    if save_scope is not None or any(t.startswith(p) for p in _SAVE_PREFIXES):
        scope = save_scope or "all"
        match = re.search(r"\bas\s+(\S+)", text, re.IGNORECASE)
        return {"intent": "save_results", "name": match.group(1) if match else None, "scope": scope}

    if re.match(r"^new\s+\S", text, re.IGNORECASE):
        return {"intent": "new_workflow", "name": None}

    search_match = re.match(
        r"^(?:search|find|lookup)\s+(?:for\s+)?(?:a\s+)?(?:ligand\s+)?(?:named\s+)?(.+)$",
        text,
        re.IGNORECASE,
    )
    if search_match:
        query = search_match.group(1).strip()
        return {"intent": "search_ligand", "name": query or None}

    # Before the rerun prefix ("dock with vina" switches, then redocks) and
    # the generic update prefix ("use vina", "set docking_algorithm vina").
    algorithm_command = detect_docking_algorithm_command(text)
    if algorithm_command is not None:
        return {
            "intent": "set_docking_algorithm",
            "name": algorithm_command["algorithm"],
            "rerun": algorithm_command["rerun"],
        }

    if re.match(r"^(rerun|run|dock|execute)\b", t):
        return {"intent": "rerun_steps", "name": None}

    if re.match(r"^use\s+pymol\b", t):
        return {"intent": "use_pymol_box", "name": None}

    # Must run before the generic update_parameters prefix match below --
    # "set up box center as protein center" and "use protein center" both
    # start with a word that prefix matches on.
    #
    # NOTE (Review Stage R1, Phase 12): unlike every other intent here,
    # set_protein_geometry has no LLM-classifier fallback -- a paraphrase
    # outside these two regexes (e.g. "put the box in the middle of the
    # receptor") falls through to update_parameters, which correctly
    # refuses to invent center/box_size but gives no hint this feature
    # exists. Deliberately deferred, not missed: see dev_plan.md Phase 12
    # Future Work ("LLM-based recognition of this command"). The numeric
    # computation would stay fully deterministic either way -- only intent
    # *recognition* would gain an LLM tier, mirroring use_pymol_box's
    # existing two-tier setup. Revisit if real usage shows a common
    # phrasing the regexes below miss.
    geometry_target = detect_protein_geometry_command(text)
    if geometry_target is not None:
        return {"intent": "set_protein_geometry", "name": geometry_target}

    # Common parameter-update prefixes — avoid LLM intent classification.
    if re.match(r"^(change|set|use|update|switch)\s+", t):
        return {"intent": "update_parameters", "name": None}

    return None


_CHAIN_BOUNDARY_RE = re.compile(r"\s+(?:and\s+then|then|and)\s+", re.IGNORECASE)


def split_command_sequence(text: str) -> List[str]:
    """Split *text* into ordered command segments at conjunction boundaries.

    A boundary (``" and "``, ``" then "``, ``" and then "``) is only accepted
    when the text immediately following it independently matches
    :func:`_deterministic_intent` — i.e. looks like the start of its own
    complete command. This keeps a single compound command like
    ``"set center 5,6,7 and box 20,20,20"`` (whose tail ``"box 20,20,20"``
    matches no known command prefix) from being incorrectly split, while
    still splitting ``"change ligand to HEM and rerun docking"`` (whose tail
    ``"rerun docking"`` matches ``rerun_steps``).

    Returns ``[text.strip()]`` unchanged when no valid boundary is found —
    callers use ``len(result) == 1`` to detect "no chain detected".
    """
    stripped = text.strip()
    segments: List[str] = []
    head = 0
    found_cut = False

    for match in _CHAIN_BOUNDARY_RE.finditer(stripped):
        tail = stripped[match.end():].strip()
        if not tail or _deterministic_intent(tail) is None:
            continue
        segment = stripped[head:match.start()].strip()
        if not segment:
            continue
        segments.append(segment)
        head = match.end()
        found_cut = True

    if not found_cut:
        return [stripped]

    tail = stripped[head:].strip()
    if tail:
        segments.append(tail)
    return segments


_SEGMENT_SPLIT_SYSTEM = (
    "You split one user input into an ordered list of separate, complete "
    "follow-up commands for an active molecular docking session, when it "
    "genuinely contains more than one command chained together (for example "
    "with 'and' or 'then'). Each command must be a complete, standalone "
    "instruction someone could type on its own, using words from the "
    "original input only:\n"
    "- new_workflow, search_ligand, update_parameters, use_pymol_box, "
    "rerun_steps, show_state, show_history, undo_revision, save_results, "
    "end_session, help\n\n"
    'Return JSON only with this exact shape: {"segments": ["<command 1>", "<command 2>", ...]}. '
    "If the input is really just one command, return it unchanged as the only element. "
    "Never invent words that are not in the original input. "
    "Never include markdown, explanations, or code fences."
)


def _llm_split_command_sequence(llm_client: BaseLLMClient, text: str) -> List[str]:
    """Ask the LLM to split *text* into ordered plain-text command segments.

    Used only as a second attempt after :func:`split_command_sequence` finds
    no valid boundary. Every proposed segment is a plain natural-language
    command string (not a pre-resolved intent/parameter object) so it can
    re-enter the normal :func:`classify_session_intent` /
    ``extract_updates_from_command_with_llm`` pipeline unchanged, exactly like
    a deterministically-split segment would.

    Returns ``[text]`` unchanged on any failure to produce a validated,
    multi-segment split: malformed/missing JSON, fewer than two segments, a
    segment that classifies to ``"unknown"``, a segment dropping words from
    the original input, or an LLM/network error.
    """
    stripped = text.strip()
    prompt = f"Split this session command if it contains more than one command:\n\n{stripped}"
    try:
        raw = llm_client.generate(prompt, system=_SEGMENT_SPLIT_SYSTEM, temperature=0.1)
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning("LLM command-sequence split failed for %r: %s", stripped, exc)
        return [stripped]

    payload = _extract_json_object(raw)
    if not payload:
        return [stripped]

    segments = payload.get("segments")
    if not isinstance(segments, list) or len(segments) < 2:
        return [stripped]

    cleaned: List[str] = []
    for segment in segments:
        if not isinstance(segment, str) or not segment.strip():
            return [stripped]
        cleaned.append(segment.strip())

    # Anti-hallucination check: every original word must reappear somewhere
    # across the returned segments (whitespace/punctuation may be normalized,
    # but no word may be dropped or invented). The connector word itself
    # ("and"/"then") is expected to disappear — it's the separator, not
    # content — so it's excluded from the original word count.
    original_words = re.sub(r"\b(?:and|then)\b", " ", stripped, flags=re.IGNORECASE).split()
    combined_words = " ".join(cleaned).split()
    if len(combined_words) < len(original_words):
        return [stripped]

    for segment in cleaned:
        if classify_session_intent(llm_client, segment)["intent"] == "unknown":
            return [stripped]

    return cleaned


def classify_session_intent(
    llm_client: BaseLLMClient,
    user_input: str,
) -> Dict[str, Any]:
    """Classify a follow-up command inside an active interactive session.

    Runs deterministic pattern matching first; falls back to LLM for ambiguous inputs.

    Returns a dict ``{"intent": str, "name": Optional[str]}`` where *name* carries
    the save-bundle name (save_results) or revision number (undo_revision) when present.
    """
    det = _deterministic_intent(user_input)
    if det is not None:
        return det

    prompt = f"Classify this session command:\n\n{user_input}"
    try:
        raw = llm_client.generate(prompt, system=_INTENT_CLASSIFIER_SYSTEM, temperature=0.1)
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning("Intent classification failed for %r: %s", user_input, exc)
        return {"intent": "unknown", "name": None}

    payload = _extract_json_object(raw)
    if not payload or "intent" not in payload:
        return {"intent": "unknown", "name": None}

    intent = payload.get("intent", "unknown")
    if intent not in _SESSION_INTENTS:
        intent = "unknown"

    scope = payload.get("scope", "all")
    if scope not in ("docking", "visualization", "all"):
        scope = "all"
    return {"intent": intent, "name": payload.get("name"), "scope": scope}


def extract_updates_from_command_with_llm(
    llm_client: BaseLLMClient,
    user_input: str,
) -> Dict[str, Any]:
    """Extract all docking parameter updates from a free-form natural language command.

    Unlike :func:`extract_updates_with_llm`, this does not require a target parameter name.
    Returns a dict of parameter updates (may be empty if none could be extracted).
    """
    prompt = (
        "Extract all workflow parameter updates from this command:\n\n"
        f"{user_input}\n\n"
        "Allowed fields: protein_id, ligand_ids, center, box_size, "
        "ph, exhaustiveness, cpu, population, num_modes, energy_range, docking_algorithm."
    )
    try:
        raw = llm_client.generate(prompt, system=_EXTRACT_PARAM_UPDATES_FROM_COMMAND_SYSTEM, temperature=0.1)
    except (requests.RequestException, RuntimeError) as exc:
        logger.warning(f"Parameter extraction from command failed: {exc}")
        return {}

    payload = _extract_json_object(raw)
    if not payload:
        return {}

    updates = payload.get("parameter_updates", {})
    if not isinstance(updates, dict):
        return {}

    return updates
