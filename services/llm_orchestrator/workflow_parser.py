"""
Workflow Parser

Validates and processes parsed workflow from LLM.
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from common.docking_algorithm import normalize_docking_algorithm
from common.utils import whole_number
from services.llm_orchestrator.workflow_definition import (
    REQUIRED_PARAMETERS_BY_STEP,
    get_default_parameters,
    get_ligand_display_names,
    get_protein_display_name,
)

logger = logging.getLogger(__name__)


def _normalize_docking_algorithm_in_place(params: Dict[str, Any]) -> None:
    """Replace a recognised docking_algorithm alias ("PSO", "AutoDock Vina",
    ...) with its canonical name. Unrecognised values are left as they are,
    for get_parameter_issue() to report."""
    if "docking_algorithm" in params:
        canonical = normalize_docking_algorithm(params["docking_algorithm"])
        if canonical:
            params["docking_algorithm"] = canonical


def _normalize_cpu_in_place(params: Dict[str, Any]) -> None:
    """Make "cpu" a whole number >= 1 ("4.0" -> 4). A value that isn't one
    (e.g. an LLM's "all") is dropped, so the default -- or, for an update,
    the current value -- stays (Review Stage R4: it reached the form and the
    docking call as given)."""
    if "cpu" in params:
        cpu = whole_number(params["cpu"])
        if cpu is None:
            logger.warning("Ignoring cpu=%r: not a whole number >= 1", params["cpu"])
            del params["cpu"]
        else:
            params["cpu"] = cpu


class WorkflowParser:
    """Validates and enriches parsed workflow data."""
    
    def __init__(self, state_manager: Optional[Any] = None):
        self.parsed_workflow = None
        self.state_manager = state_manager

    def set_state_manager(self, state_manager: Optional[Any]) -> None:
        """Attach/detach a persistence manager for parameter checkpoints."""
        self.state_manager = state_manager

    def get_parameter_issue(self, param: str) -> Optional[str]:
        """Return issue type for required params: 'missing', 'invalid', or None."""
        if not self.parsed_workflow:
            return "missing"

        params = self.parsed_workflow.get("parameters", {})
        value = params.get(param)

        if param == "protein_id":
            if params.get("protein_file"):
                return self._validate_local_file_param(
                    params["protein_file"], allowed_extensions={".pdb", ".cif"}
                )
            if value is None or not isinstance(value, str) or not value.strip():
                return "missing"
            if not self.validate_protein_id(value.strip()):
                return "invalid"
            return None

        if param == "ligand_ids":
            if params.get("ligand_folder"):
                folder = params["ligand_folder"]
                if not isinstance(folder, str) or not folder.strip():
                    return "missing"
                if not Path(folder).is_dir():
                    return "invalid"
                return None
            if params.get("ligand_files"):
                files = params["ligand_files"]
                if not isinstance(files, list) or len(files) == 0:
                    return "missing"
                for f in files:
                    issue = self._validate_local_file_param(
                        f, allowed_extensions={".sdf", ".mol", ".mol2", ".pdbqt"}
                    )
                    if issue is not None:
                        return issue
                return None
            if value is None or not isinstance(value, list) or len(value) == 0:
                return "missing"
            return None

        if value is None:
            return "missing"

        if param == "docking_algorithm":
            return None if normalize_docking_algorithm(value) else "invalid"

        if param in ["center", "box_size"]:
            if not isinstance(value, list) or len(value) == 0:
                return "missing"
            if not self.validate_coordinates(value):
                return "invalid"
            return None

        return None

    @staticmethod
    def _validate_local_file_param(path: str, allowed_extensions: set) -> Optional[str]:
        """Return 'invalid' if a local file path doesn't exist or has the wrong extension."""
        if not isinstance(path, str) or not path.strip():
            return "missing"
        p = Path(path)
        if p.suffix.lower() not in allowed_extensions:
            return "invalid"
        if not p.is_file():
            return "invalid"
        return None

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
        
        _normalize_cpu_in_place(parameters)
        # Apply defaults
        defaults = get_default_parameters()
        for key, default_value in defaults.items():
            if key not in parameters:
                parameters[key] = default_value
        _normalize_docking_algorithm_in_place(parameters)
        
        # Publish parameters now so get_parameter_issue reads current values below.
        self.parsed_workflow = {"services": services, "parameters": parameters, "missing_required": []}

        seen: set = set()
        missing = []
        for service in services:
            if service in REQUIRED_PARAMETERS_BY_STEP:
                for param in REQUIRED_PARAMETERS_BY_STEP[service]:
                    if param not in seen and self.get_parameter_issue(param) is not None:
                        missing.append(param)
                        seen.add(param)

        # Not a required parameter (it always has a default), but an
        # unrecognised value would only fail later inside moldina-runner --
        # surface it now, through the same clarification prompt.
        if "dock" in services and self.get_parameter_issue("docking_algorithm") is not None:
            missing.append("docking_algorithm")

        self.parsed_workflow["missing_required"] = missing
        return self.parsed_workflow
    
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
            params = (self.parsed_workflow or {}).get("parameters", {})
            if param == "protein_id":
                if params.get("protein_file"):
                    lines.append(
                        f"  - The local protein file '{params['protein_file']}' is invalid or not found "
                        "(expected an existing .pdb or .cif file)."
                    )
                elif issue == "invalid":
                    lines.append("  - The protein PDB ID is invalid. Please provide a 4-character ID (e.g., '6lu7').")
                else:
                    lines.append("  - Please provide the protein PDB ID (e.g., '6lu7') or a local protein file.")
            elif param == "ligand_ids":
                if params.get("ligand_folder"):
                    lines.append(
                        f"  - The ligand folder '{params['ligand_folder']}' is invalid or not found."
                    )
                elif params.get("ligand_files"):
                    lines.append(
                        "  - One or more local ligand files are invalid or not found "
                        "(expected existing .sdf, .mol, .mol2 or .pdbqt files)."
                    )
                else:
                    lines.append("  - Please provide ligand IDs (e.g., 'ATP, HEM'), local ligand files, or a ligand folder.")
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
            elif param == "docking_algorithm":
                lines.append(
                    f"  - Docking algorithm '{params.get('docking_algorithm')}' is not recognised. "
                    "Use 'moldina' (PSO) or 'vina' (Monte Carlo)."
                )
            else:
                lines.append(f"  - Please provide {param}.")
        
        return "\n".join(lines)
    
    def update_parameters(
        self,
        updates: Dict[str, Any],
        source: str = "parameter_update",
        persist: bool = True,
    ) -> None:
        """
        Update workflow parameters.
        
        Args:
            updates: Dictionary of parameter updates
            source: Update source for persistence metadata
            persist: Whether to persist this change via state_manager
        """
        if not self.parsed_workflow:
            raise ValueError("No workflow loaded")
        
        updates = dict(updates)
        _normalize_docking_algorithm_in_place(updates)
        _normalize_cpu_in_place(updates)
        self.parsed_workflow["parameters"].update(updates)
        
        # Remove from missing list if now provided
        missing = self.parsed_workflow.get("missing_required", [])
        self.parsed_workflow["missing_required"] = [p for p in missing if p not in updates]

        if persist and self.state_manager:
            self.state_manager.save_parameters_checkpoint(
                self.parsed_workflow.get("parameters", {}),
                source=source,
            )
    
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
        
        protein_display = get_protein_display_name(params)
        if protein_display:
            lines.append(f"  Protein: {protein_display}")
        ligand_display = get_ligand_display_names(params)
        if ligand_display:
            lines.append(f"  Ligands: {', '.join(ligand_display)}")
        if "center" in params:
            lines.append(f"  Docking Center: {params['center']}")
        if "box_size" in params:
            lines.append(f"  Box Size: {params['box_size']}")
        if "ph" in params:
            lines.append(f"  pH: {params['ph']}")
        if "docking_algorithm" in params:
            lines.append(f"  Docking Algorithm: {params['docking_algorithm']}")
        if "exhaustiveness" in params:
            lines.append(f"  Exhaustiveness: {params['exhaustiveness']}")
        if "cpu" in params:
            lines.append(f"  CPUs: {params['cpu']}")
        
        return "\n".join(lines)
