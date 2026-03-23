"""
Workflow Parser

Validates and processes parsed workflow from LLM.
"""
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkflowParser:
    """Validates and enriches parsed workflow data."""
    
    # Default parameters
    DEFAULTS = {
        "ph": 7.4,
        "exhaustiveness": 8,
        "cpu": 4,
        "population": 500,
        "num_modes": 15,
        "energy_range": 100.0,
        "add_hydrogens": True,
        "remove_waters": True,
        "generate_3d": True,
        "optimize": True,
        "formats": {"protein": "pdb", "ligand": "sdf"}
    }
    
    # Required parameters for each service
    REQUIRED = {
        "download": ["protein_id"],
        "prepare_ligands": ["ligand_ids"],
        "prepare_protein": ["protein_id"],
        "dock": ["protein_id", "ligand_ids", "center", "box_size"],
        "visualize": ["protein_id", "ligand_ids"]
    }
    
    def __init__(self):
        self.parsed_workflow = None

    def get_parameter_issue(self, param: str) -> Optional[str]:
        """Return issue type for required params: 'missing', 'invalid', or None."""
        if not self.parsed_workflow:
            return "missing"

        value = self.parsed_workflow.get("parameters", {}).get(param)

        if value is None:
            return "missing"

        if param == "protein_id":
            if not isinstance(value, str) or not value.strip():
                return "missing"
            if not self.validate_protein_id(value.strip()):
                return "invalid"
            return None

        if param == "ligand_ids":
            if not isinstance(value, list) or len(value) == 0:
                return "missing"
            return None

        if param in ["center", "box_size"]:
            if not isinstance(value, list) or len(value) == 0:
                return "missing"
            if not self.validate_coordinates(value):
                return "invalid"
            return None

        return None

    def _is_missing_required_param(self, param: str, value: Any) -> bool:
        """Return True when a required parameter is absent or invalid."""
        if value is None:
            return True

        if param == "protein_id":
            return not isinstance(value, str) or not self.validate_protein_id(value.strip())

        if param == "ligand_ids":
            return not isinstance(value, list) or len(value) == 0

        if param in ["center", "box_size"]:
            return not self.validate_coordinates(value)

        return False
    
    def validate_workflow(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate parsed workflow and fill in defaults.
        
        Args:
            parsed: Parsed workflow from LLM
            
        Returns:
            Validated and enriched workflow
        """
        if not parsed or "services" not in parsed:
            raise ValueError("Invalid workflow: missing 'services' field")
        
        services = parsed.get("services", [])
        parameters = parsed.get("parameters", {})
        
        # Apply defaults
        for key, default_value in self.DEFAULTS.items():
            if key not in parameters:
                parameters[key] = default_value
        
        # Check for missing required parameters
        missing = []
        for service in services:
            if service in self.REQUIRED:
                for param in self.REQUIRED[service]:
                    if self._is_missing_required_param(param, parameters.get(param)):
                        missing.append(param)
        
        # Remove duplicates
        missing = list(set(missing))
        
        validated = {
            "services": services,
            "parameters": parameters,
            "missing_required": missing
        }
        
        self.parsed_workflow = validated
        return validated
    
    def has_missing_parameters(self) -> bool:
        """Check if workflow has missing required parameters."""
        if not self.parsed_workflow:
            return True
        return len(self.parsed_workflow.get("missing_required", [])) > 0
    
    def get_missing_parameters(self) -> List[str]:
        """Get list of missing required parameters."""
        if not self.parsed_workflow:
            return []
        return self.parsed_workflow.get("missing_required", [])
    
    def format_missing_prompt(self) -> str:
        """Generate user prompt for missing parameters."""
        missing = self.get_missing_parameters()
        if not missing:
            return ""
        
        lines = ["Required parameters needing clarification:"]
        for param in missing:
            issue = self.get_parameter_issue(param)
            if param == "protein_id":
                if issue == "invalid":
                    lines.append("  - The protein PDB ID is invalid. Please provide a 4-character ID (e.g., '6lu7').")
                else:
                    lines.append("  - Please provide the protein PDB ID (e.g., '6lu7').")
            elif param == "ligand_ids":
                lines.append("  - Please provide ligand IDs (e.g., 'ATP, HEM').")
            elif param == "center":
                if issue == "invalid":
                    lines.append("  - Docking box center is invalid. Please provide [x, y, z] (e.g., '10, 20, 30').")
                else:
                    lines.append("  - Please provide docking box center coordinates [x, y, z] (e.g., '10, 20, 30').")
            elif param == "box_size":
                if issue == "invalid":
                    lines.append("  - Docking box size is invalid. Please provide [x, y, z] in Angstroms (e.g., '25, 30, 35').")
                else:
                    lines.append("  - Please provide docking box size [x, y, z] in Angstroms (e.g., '25, 30, 35').")
            else:
                lines.append(f"  - Please provide {param}.")
        
        return "\n".join(lines)
    
    def update_parameters(self, updates: Dict[str, Any]) -> None:
        """
        Update workflow parameters.
        
        Args:
            updates: Dictionary of parameter updates
        """
        if not self.parsed_workflow:
            raise ValueError("No workflow loaded")
        
        self.parsed_workflow["parameters"].update(updates)
        
        # Remove from missing list if now provided
        missing = self.parsed_workflow.get("missing_required", [])
        for key in updates.keys():
            if key in missing:
                missing.remove(key)
    
    def validate_protein_id(self, protein_id: str) -> bool:
        """Validate PDB ID format (4 characters)."""
        return len(protein_id) == 4 and protein_id.isalnum()
    
    def validate_coordinates(self, coords: List[float]) -> bool:
        """Validate coordinate list [x, y, z]."""
        return isinstance(coords, list) and len(coords) == 3 and all(isinstance(x, (int, float)) for x in coords)
    
    def get_workflow_summary(self) -> str:
        """Generate human-readable workflow summary."""
        if not self.parsed_workflow:
            return "No workflow loaded"
        
        services = self.parsed_workflow.get("services", [])
        params = self.parsed_workflow.get("parameters", {})
        
        lines = ["Workflow Summary:", "=" * 50]
        lines.append(f"Services: {', '.join(services)}")
        lines.append("")
        lines.append("Parameters:")
        
        if "protein_id" in params:
            lines.append(f"  Protein: {params['protein_id']}")
        if "ligand_ids" in params:
            lines.append(f"  Ligands: {', '.join(params['ligand_ids'])}")
        if "center" in params:
            lines.append(f"  Docking Center: {params['center']}")
        if "box_size" in params:
            lines.append(f"  Box Size: {params['box_size']}")
        if "ph" in params:
            lines.append(f"  pH: {params['ph']}")
        if "exhaustiveness" in params:
            lines.append(f"  Exhaustiveness: {params['exhaustiveness']}")
        if "cpu" in params:
            lines.append(f"  CPUs: {params['cpu']}")
        
        return "\n".join(lines)
