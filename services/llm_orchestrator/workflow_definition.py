"""Centralized workflow metadata and helpers.

This module is the source of truth for:
- workflow step order
- required parameters per step
- default workflow parameters
- parameter invalidation rules for partial reruns
- MCP service endpoint defaults
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional


WORKFLOW_STEPS: List[str] = [
    "get_input_files",
    "prepare_ligands",
    "prepare_protein",
    "dock",
    "visualize",
]


DEFAULT_PARAMETERS: Dict[str, object] = {
    "ph": 7.4,
    "exhaustiveness": 8,
    "cpu": 1,
    "population": 500,
    "num_modes": 15,
    "energy_range": 100.0,
    "parallel": False,
    "max_workers": 1,
    "add_hydrogens": True,
    "remove_waters": True,
    "generate_3d": True,
    "optimize": True,
    "formats": {"protein": "pdb", "ligand": "sdf"},
    "protein_file": None,
    "ligand_files": None,
    "ligand_folder": None,
}


CORE_PARAMS: FrozenSet[str] = frozenset({"protein_id", "ligand_ids", "center", "box_size"})

REQUIRED_PARAMETERS_BY_STEP: Dict[str, List[str]] = {
    "get_input_files": ["protein_id"],
    "prepare_ligands": ["ligand_ids"],
    "prepare_protein": ["protein_id"],
    "dock": ["protein_id", "ligand_ids", "center", "box_size"],
    "visualize": ["protein_id", "ligand_ids"],
}


PARAM_INVALIDATION_MAP: Dict[str, FrozenSet[str]] = {
    "protein_id": frozenset({"get_input_files", "prepare_protein", "dock", "visualize"}),
    "ligand_ids": frozenset({"get_input_files", "prepare_ligands", "dock", "visualize"}),
    "center": frozenset({"dock", "visualize"}),
    "box_size": frozenset({"dock", "visualize"}),
    "ph": frozenset({"prepare_ligands", "prepare_protein", "dock", "visualize"}),
    "exhaustiveness": frozenset({"dock", "visualize"}),
    "cpu": frozenset({"dock", "visualize"}),
    "population": frozenset({"dock", "visualize"}),
    "num_modes": frozenset({"dock", "visualize"}),
    "energy_range": frozenset({"dock", "visualize"}),
    "parallel": frozenset({"dock", "visualize"}),
    "max_workers": frozenset({"dock", "visualize"}),
}


STEP_UPSTREAM: Dict[str, FrozenSet[str]] = {
    "get_input_files": frozenset(),
    "prepare_ligands": frozenset({"get_input_files"}),
    "prepare_protein": frozenset({"get_input_files"}),
    "dock": frozenset({"prepare_ligands", "prepare_protein"}),
    "visualize": frozenset({"dock"}),
}


STEP_LABELS: Dict[str, str] = {
    "get_input_files": "Get input files",
    "prepare_ligands": "Prepare ligands",
    "prepare_protein": "Prepare protein",
    "dock": "Run docking",
    "visualize": "Generate visualization",
}


MCP_SERVICE_DEFAULT_URLS: Dict[str, str] = {
    "rcsb_downloader": "http://localhost:8001",
    "ligand_preparer": "http://localhost:8002",
    "protein_preparer": "http://localhost:8003",
    "moldina_runner": "http://localhost:8004",
    "pymol_visualizer": "http://localhost:8005",
}


MCP_SERVICE_URL_ENV_VARS: Dict[str, str] = {
    "rcsb_downloader": "MCP_RCSB_DOWNLOADER_URL",
    "ligand_preparer": "MCP_LIGAND_PREPARER_URL",
    "protein_preparer": "MCP_PROTEIN_PREPARER_URL",
    "moldina_runner": "MCP_MOLDINA_RUNNER_URL",
    "pymol_visualizer": "MCP_PYMOL_VISUALIZER_URL",
}


def get_default_parameters() -> Dict[str, object]:
    """Return a deep-copied defaults dictionary safe for mutation."""
    return copy.deepcopy(DEFAULT_PARAMETERS)


def get_service_urls_from_env() -> Dict[str, str]:
    """Build service endpoint map using env overrides over defaults."""
    urls: Dict[str, str] = {}
    for service_name, default_url in MCP_SERVICE_DEFAULT_URLS.items():
        env_key = MCP_SERVICE_URL_ENV_VARS[service_name]
        urls[service_name] = os.getenv(env_key, default_url)
    return urls


def get_protein_display_name(parameters: Dict[str, Any]) -> Optional[str]:
    """Return a human-readable protein label: the local file's stem, or the PDB ID.

    A local protein_file always wins over protein_id at execution time
    (see WorkflowOrchestrator.execute_workflow), so the display must match.
    """
    protein_file = parameters.get("protein_file")
    if protein_file:
        return f"{Path(protein_file).stem} (local file)"
    protein_id = parameters.get("protein_id")
    if protein_id:
        return str(protein_id)
    return None


def get_ligand_display_names(parameters: Dict[str, Any]) -> List[str]:
    """Return human-readable ligand labels: local file stems plus PDB ligand IDs.

    Unlike protein_file/protein_id, ligand_files and ligand_ids are combined (not
    mutually exclusive) at execution time (see WorkflowOrchestrator.execute_workflow),
    so the display must list both sources. ligand_folder supersedes both (it is
    expanded into ligand_files at execution time), so it's shown on its own.
    """
    ligand_folder = parameters.get("ligand_folder")
    if ligand_folder:
        return [f"all files in {ligand_folder} (folder)"]

    names: List[str] = []
    ligand_files = parameters.get("ligand_files")
    if ligand_files:
        names.extend(f"{Path(f).stem} (local file)" for f in ligand_files)
    ligand_ids = parameters.get("ligand_ids")
    if ligand_ids:
        names.extend(str(lid) for lid in ligand_ids)
    return names
