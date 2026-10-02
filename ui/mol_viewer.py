"""Python wrapper around the vendored 3Dmol.js Streamlit component.

Replaces the PyMOL-rendered PNG / noVNC desktop for viewing docking results,
and (in "box_picker" mode) provides a click-to-set-center + live box overlay
for defining the Vina search box -- see docs/ for the wider PyMOL -> 3Dmol.js
migration this is part of.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent / "components" / "mol_viewer"

_mol_viewer = components.declare_component("mol_viewer", path=str(_COMPONENT_DIR))


def render_mol_viewer(
    receptor_pdb: str,
    ligands: Optional[Dict[str, Dict[str, Any]]] = None,
    mode: str = "view",
    center: Optional[List[float]] = None,
    box_size: Optional[List[float]] = None,
    height: int = 500,
    key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Render the shared 3Dmol.js viewer/box-picker component.

    Args:
        receptor_pdb: Receptor structure as PDB text (cartoon style).
        ligands: ``{ligand_id: {"sdf": <SDF text, one pose>, "affinity": float|None}}``.
            Poses are colored green (best) -> red (worst) by affinity when at
            least one ligand has a numeric affinity, otherwise a flat color.
        mode: ``"view"`` (read-only results viewer) or ``"box_picker"``
            (adds a live transparent box overlay and click-to-set-center on
            the receptor).
        center: Current box center ``[x, y, z]`` (box_picker mode).
        box_size: Current box dimensions ``[x, y, z]`` (box_picker mode).
        height: Viewer height in pixels.
        key: Streamlit widget key -- must be distinct per simultaneous
            instance (e.g. results viewer vs. box picker).

    Returns:
        In box_picker mode, ``{"center": [x, y, z], "clickId": <ms timestamp>}``
        for the most recently clicked receptor atom, or ``None`` if nothing
        has been clicked yet. The same value comes back on every rerun until
        the next click; ``clickId`` tells a new click (even on the same atom)
        from that repeat.
    """
    return _mol_viewer(
        receptorPdb=receptor_pdb,
        ligandSdfs=ligands or {},
        mode=mode,
        center=center,
        boxSize=box_size,
        height=height,
        key=key,
        default=None,
    )
