"""Selectable docking search algorithm.

The committed ``moldina`` binary runs two searches: Moldina's Particle Swarm
Optimization (``--search_alg_PSO --population N``) and, with both flags
omitted, the AutoDock Vina 1.2.x Monte Carlo search of the fork it is built
on (``docs/MOLDINA_DOCKING.md`` § Vina mode). ``docking_algorithm`` picks
between them. Shared by ``moldina-runner`` (builds the command), the
orchestrator (defaults and validation) and the chat front ends (labels), so
the allowed values and aliases have one definition.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

DOCKING_ALGORITHMS: Tuple[str, ...] = ("moldina", "vina")
DEFAULT_DOCKING_ALGORITHM = "moldina"

# Human-readable names for replies and the GUI selector.
DOCKING_ALGORITHM_LABELS: Dict[str, str] = {
    "moldina": "Moldina (PSO)",
    "vina": "Vina (Monte Carlo)",
}

_ALIASES: Dict[str, str] = {
    "moldina": "moldina",
    "pso": "moldina",
    "moldina pso": "moldina",
    "vina": "vina",
    "autodock vina": "vina",
    "monte carlo": "vina",
    "vina monte carlo": "vina",
}

_SEPARATORS = str.maketrans({"-": " ", "_": " ", "(": " ", ")": " "})


def normalize_docking_algorithm(value: Any) -> Optional[str]:
    """Return the canonical algorithm name for *value*, or None if unrecognised.

    Case-insensitive; collapses whitespace and treats ``-``, ``_`` and
    parentheses as spaces, so "Monte-Carlo", "autodock_vina" and the GUI
    labels ("Vina (Monte Carlo)") are accepted too.
    """
    if not isinstance(value, str):
        return None
    key = " ".join(value.lower().translate(_SEPARATORS).split())
    return _ALIASES.get(key)


def docking_algorithm_label(value: Any) -> str:
    """Human-readable name for *value*; an unknown value is shown as is."""
    return DOCKING_ALGORITHM_LABELS.get(value, str(value))


def docking_algorithm_of(dock_result: Optional[Dict[str, Any]]) -> str:
    """Which algorithm produced a ``run_docking`` result. A result without
    a ``docking_algorithm`` key (written by an older version) was
    Moldina (PSO), the default."""
    return (dock_result or {}).get("docking_algorithm") or DEFAULT_DOCKING_ALGORITHM


def cpus_per_ligand(algorithm: str, cpu: int) -> int:
    """CPUs one docking process really uses: *cpu* for Vina, whose Monte
    Carlo search is multithreaded, and 1 for Moldina (PSO), which ignores
    ``--cpu`` (measured, see docs/MOLDINA_DOCKING.md). The one
    rule behind both the server's worker clamp (quota // this) and the
    GUI's worker maximum."""
    return max(1, int(cpu)) if algorithm == "vina" else 1
