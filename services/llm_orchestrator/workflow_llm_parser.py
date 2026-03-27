"""
Provider-neutral workflow parsing using an LLM client interface.
"""
import json
import logging
from typing import Any, Dict, Optional

from .llm_base import BaseLLMClient
from .workflow_parser import WorkflowParser

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a molecular docking workflow parser. Extract structured information from user requests.

Extract the following:
1. Services needed (choose from: download, prepare_ligands, prepare_protein, dock, visualize)
   - Always include "download" if protein/ligand IDs are mentioned
   - Always include "prepare_ligands" and "prepare_protein" if preparing or docking
   - Always include "dock" if docking is requested
   - Include "visualize" if user mentions ANY of these keywords: visualize, visualization, visualise, visualisation, view, render, rendering, image, picture, photo, show results, create visualization, display, see results, generate image

2. Parameters:
   - protein_id (4-character PDB ID)
   - ligand_ids (list of ligand names - CRITICAL: If user specifies multiple copies of the same ligand, you MUST duplicate the ligand ID in the array!)
   - center (docking box center [x, y, z])
   - box_size (docking box dimensions [x, y, z])
   - ph (pH value, default 7.4)
   - exhaustiveness (docking thoroughness, default 8)
   - cpu (number of CPUs, default 4)
   - population (PSO population, default 500)

CRITICAL RULE FOR LIGAND_IDS:
- If user says "two WWO ligands", you MUST output: ["WWO", "WWO"]
- If user says "three ATP molecules", you MUST output: ["ATP", "ATP", "ATP"]
- If user says "dock protein and two WWO ligands", you MUST output: ["WWO", "WWO"]
- The array must contain the EXACT number of copies specified by the user!

Correct Examples:
User: "Dock 6lu7 with ATP" -> ligand_ids: ["ATP"], services: ["download", "prepare_ligands", "prepare_protein", "dock"]
User: "Dock protein with two WWO ligands" -> ligand_ids: ["WWO", "WWO"]
User: "Dock 5ogo and two WWO ligands" -> ligand_ids: ["WWO", "WWO"]
User: "Use three ATP molecules" -> ligand_ids: ["ATP", "ATP", "ATP"]
User: "Dock with ATP and two HEM" -> ligand_ids: ["ATP", "HEM", "HEM"]
User: "Dock and create visualization" -> services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock with ATP and render image" -> services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock and show results" -> services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]

Wrong Examples (DO NOT DO THIS):
User: "Dock with two WWO ligands" -> ligand_ids: ["WWO"] WRONG! Should be ["WWO", "WWO"]
User: "Use three ATP" -> ligand_ids: ["ATP"] WRONG! Should be ["ATP", "ATP", "ATP"]

Respond ONLY with valid JSON in this exact format:
{
  "services": ["download", "prepare_ligands", "prepare_protein", "dock"],
  "parameters": {
    "protein_id": "5ogo",
    "ligand_ids": ["WWO", "WWO"],
    "center": [16.0, 9.0, 12.0],
    "box_size": [20.0, 20.0, 20.0],
    "ph": 7.4,
    "exhaustiveness": 8,
    "cpu": 4,
    "population": 500
  },
  "missing_required": []
}

If critical parameters (center, box_size) are missing, list them in "missing_required".
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

    return None


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

    visualization_keywords = [
        'visualiz', 'visualis', 'render', 'image', 'picture',
        'photo', 'view', 'display', 'show', 'see results', 'create vis'
    ]
    user_input_lower = user_input.lower()
    if any(keyword in user_input_lower for keyword in visualization_keywords):
        services = parsed.get('services', [])
        if 'visualize' not in services:
            logger.info("Adding 'visualize' service based on keyword detection in user input")
            services.append('visualize')
            parsed['services'] = services

    return parsed


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
    except Exception as exc:
        logger.warning(f"Failed to generate LLM follow-up question for '{current_param}': {exc}")

    # Deterministic fallback when LLM call fails.
    if current_param == "protein_id":
        if issue == "invalid":
            return "The protein PDB ID looks invalid. Please provide a valid 4-character ID (e.g., 6lu7):"
        return "I am missing the protein PDB ID. Please provide it (e.g., 6lu7):"
    if current_param == "ligand_ids":
        return "I am missing ligand IDs. Please provide comma-separated IDs (e.g., ATP,HEM):"
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
        "3. Allowed fields only: protein_id, ligand_ids, center, box_size, ph, exhaustiveness, cpu, population, num_modes, energy_range.\n"
        "4. Never infer missing scientific values from defaults, prior examples, or typical practice.\n"
        '5. If the user answer is ambiguous, partial, contradictory, or not machine-usable, return {"parameter_updates": {}}.\n'
        "6. Keep existing workflow capabilities intact: do not invent new services or new parameter names.\n"
        "7. Deterministic UI choices like manual/default/PyMOL selection are handled elsewhere; only extract concrete parameter values from the answer.\n\n"
        "Field constraints:\n"
        '- protein_id: exactly one 4-character alphanumeric string, for example "6lu7".\n'
        "- ligand_ids: JSON array of strings. Preserve multiplicity exactly when the user specifies counts.\n"
        '- Count words must be expanded when explicit, e.g. one/two/three ATP -> ["ATP"], ["ATP", "ATP"], ["ATP", "ATP", "ATP"].\n'
        '- center and box_size: JSON array of exactly 3 numbers, extracted from forms like "10,20,30" or "[10, 20, 30]".\n'
        "- Numeric scalar fields must be returned as numbers, not strings.\n\n"
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
    except Exception as exc:
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
    except Exception as exc:
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
