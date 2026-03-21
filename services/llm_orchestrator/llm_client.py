"""
Ollama LLM Client

Handles communication with the Ollama API for natural language processing.
"""
import requests
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for interacting with Ollama LLM API."""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama2"):
        """
        Initialize Ollama client.
        
        Args:
            base_url: URL of Ollama API server
            model: Name of the model to use (llama2, mistral, phi3, etc.)
        """
        self.base_url = base_url
        self.model = model
        self.api_url = f"{base_url}/api/generate"
        
    def check_health(self) -> bool:
        """Check if Ollama service is running."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except (requests.RequestException, Exception) as e:
            logger.error(f"Ollama health check failed: {e}")
            return False
    
    def generate(self, prompt: str, system: str = None, temperature: float = 0.1) -> str:
        """
        Generate text using Ollama.
        
        Args:
            prompt: User prompt
            system: System prompt (instructions)
            temperature: Sampling temperature (0.0-1.0, lower = more deterministic)
            
        Returns:
            Generated text response
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        
        if system:
            payload["system"] = system
        
        try:
            logger.info(f"Calling Ollama with model: {self.model}")
            response = requests.post(self.api_url, json=payload, timeout=120)
            response.raise_for_status()
            
            result = response.json()
            return result.get("response", "")
            
        except requests.RequestException as e:
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
    
    def parse_workflow(self, user_input: str) -> Dict[str, Any]:
        """
        Parse natural language workflow request into structured format.
        
        Args:
            user_input: Natural language description of workflow
            
        Returns:
            Parsed workflow as dictionary
        """
        system_prompt = """You are a molecular docking workflow parser. Extract structured information from user requests.

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
User: "Dock 6lu7 with ATP" → ligand_ids: ["ATP"], services: ["download", "prepare_ligands", "prepare_protein", "dock"]
User: "Dock protein with two WWO ligands" → ligand_ids: ["WWO", "WWO"]
User: "Dock 5ogo and two WWO ligands" → ligand_ids: ["WWO", "WWO"]
User: "Use three ATP molecules" → ligand_ids: ["ATP", "ATP", "ATP"]
User: "Dock with ATP and two HEM" → ligand_ids: ["ATP", "HEM", "HEM"]
User: "Dock and create visualization" → services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock with ATP and render image" → services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]
User: "Dock and show results" → services: ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"]

Wrong Examples (DO NOT DO THIS):
User: "Dock with two WWO ligands" → ligand_ids: ["WWO"] ❌ WRONG! Should be ["WWO", "WWO"]
User: "Use three ATP" → ligand_ids: ["ATP"] ❌ WRONG! Should be ["ATP", "ATP", "ATP"]

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

        user_prompt = f"Parse this workflow request:\n\n{user_input}"
        
        response = self.generate(user_prompt, system=system_prompt, temperature=0.1)
        
        # Extract JSON from response (handle cases where LLM adds extra text)
        try:
            # Try to find JSON in the response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start != -1 and end > start:
                json_str = response[start:end]
                parsed = json.loads(json_str)
                logger.info(f"LLM parsed services: {parsed.get('services', [])}")
                logger.info(f"LLM parsed ligand_ids: {parsed.get('parameters', {}).get('ligand_ids', [])}")
                
                # Fallback: If LLM missed visualization keywords, check manually
                visualization_keywords = [
                    'visualiz', 'visualis', 'render', 'image', 'picture', 
                    'photo', 'view', 'display', 'show', 'see results', 'create vis'
                ]
                user_input_lower = user_input.lower()
                if any(keyword in user_input_lower for keyword in visualization_keywords):
                    services = parsed.get('services', [])
                    if 'visualize' not in services:
                        logger.info(f"Adding 'visualize' service based on keyword detection in user input")
                        services.append('visualize')
                        parsed['services'] = services
                
                return parsed
            else:
                logger.error(f"No JSON found in response: {response}")
                raise ValueError("No JSON found in LLM response")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}\nResponse: {response}")
            raise ValueError(f"Invalid JSON from LLM: {e}")
    
    def list_models(self) -> list:
        """List available models in Ollama."""
        try:
            response = requests.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except requests.RequestException as e:
            logger.error(f"Failed to list models: {e}")
            return []
