"""Selectable docking search algorithm (Phase 15, Stage 15.1).

The committed ``moldina`` binary runs two searches: Moldina's Particle Swarm
Optimization (``--search_alg_PSO --population N``) and, with both flags
omitted, the classic AutoDock Vina 1.1.2-era Monte Carlo search it was forked
from. ``docking_algorithm`` picks between them. Shared by ``moldina-runner``
(builds the command) and the orchestrator (defaults and validation), so the
allowed values and aliases have one definition.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

DOCKING_ALGORITHMS: Tuple[str, ...] = ("moldina", "vina")
DEFAULT_DOCKING_ALGORITHM = "moldina"

_ALIASES: Dict[str, str] = {
    "moldina": "moldina",
    "pso": "moldina",
    "moldina pso": "moldina",
    "moldina (pso)": "moldina",
    "vina": "vina",
    "autodock vina": "vina",
    "monte carlo": "vina",
    "vina monte carlo": "vina",
    "vina (monte carlo)": "vina",
}


def normalize_docking_algorithm(value: Any) -> Optional[str]:
    """Return the canonical algorithm name for *value*, or None if unrecognised.

    Case-insensitive; collapses whitespace and treats ``-``/``_`` as spaces,
    so "Monte-Carlo" and "autodock_vina" are accepted too.
    """
    if not isinstance(value, str):
        return None
    key = " ".join(value.lower().replace("-", " ").replace("_", " ").split())
    return _ALIASES.get(key)
