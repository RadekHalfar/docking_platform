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
from typing import Dict, FrozenSet, List


WORKFLOW_STEPS: List[str] = [
    "download",
    "prepare_ligands",
    "prepare_protein",
    "dock",
    "visualize",
]


DEFAULT_PARAMETERS: Dict[str, object] = {
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
    "formats": {"protein": "pdb", "ligand": "sdf"},
}


REQUIRED_PARAMETERS_BY_STEP: Dict[str, List[str]] = {
    "download": ["protein_id"],
    "prepare_ligands": ["ligand_ids"],
    "prepare_protein": ["protein_id"],
    "dock": ["protein_id", "ligand_ids", "center", "box_size"],
    "visualize": ["protein_id", "ligand_ids"],
}


PARAM_INVALIDATION_MAP: Dict[str, FrozenSet[str]] = {
    "protein_id": frozenset({"download", "prepare_protein", "dock", "visualize"}),
    "ligand_ids": frozenset({"download", "prepare_ligands", "dock", "visualize"}),
    "center": frozenset({"dock", "visualize"}),
    "box_size": frozenset({"dock", "visualize"}),
    "ph": frozenset({"prepare_ligands", "prepare_protein", "dock", "visualize"}),
    "exhaustiveness": frozenset({"dock", "visualize"}),
    "cpu": frozenset({"dock", "visualize"}),
    "population": frozenset({"dock", "visualize"}),
    "num_modes": frozenset({"dock", "visualize"}),
    "energy_range": frozenset({"dock", "visualize"}),
}


STEP_UPSTREAM: Dict[str, FrozenSet[str]] = {
    "download": frozenset(),
    "prepare_ligands": frozenset({"download"}),
    "prepare_protein": frozenset({"download"}),
    "dock": frozenset({"prepare_ligands", "prepare_protein"}),
    "visualize": frozenset({"dock"}),
}


STEP_LABELS: Dict[str, str] = {
    "download": "Download structures",
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
