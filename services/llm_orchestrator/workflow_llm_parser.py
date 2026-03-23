"""
Provider-neutral workflow parsing using an LLM client interface.
"""
import json
import logging
from typing import Any, Dict

from .llm_base import BaseLLMClient

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


def parse_workflow_with_llm(llm_client: BaseLLMClient, user_input: str) -> Dict[str, Any]:
    """Parse natural language workflow request into structured format."""
    user_prompt = f"Parse this workflow request:\n\n{user_input}"
    response = llm_client.generate(user_prompt, system=SYSTEM_PROMPT, temperature=0.1)

    try:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end <= start:
            logger.error(f"No JSON found in response: {response}")
            raise ValueError("No JSON found in LLM response")

        json_str = response[start:end]
        parsed = json.loads(json_str)
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
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON response: {e}\nResponse: {response}")
        raise ValueError(f"Invalid JSON from LLM: {e}")
