"""Protein bounding-box geometry from raw PDB text.

Used to auto-fill the docking box (`center`, `box_size`) from a prepared
protein structure's own atom coordinates -- "set box center as protein
center" / "look across all protein" chat commands (GUI: `ui/app.py`, CLI:
`nlp_orchestrator.py`), both routed through
`services/llm_orchestrator/workflow_llm_parser.py::detect_protein_geometry_command()`.
"""
from __future__ import annotations

from typing import List, Tuple

DEFAULT_PADDING_FRACTION = 0.10


def parse_atom_coordinates(pdb_text: str) -> List[Tuple[float, float, float]]:
    """Extract (x, y, z) from every ``ATOM`` record in *pdb_text*.

    ``HETATM`` records (waters, cofactors, ions) are deliberately excluded --
    this is what makes the result "protein" geometry rather than "whatever
    is left in the file" geometry, independent of the
    `remove_waters`/`remove_heteroatoms` settings used to prepare it. Uses
    fixed-column PDB parsing (columns 31-38/39-46/47-54 for x/y/z, 1-indexed
    per the PDB format spec); a line that doesn't parse cleanly is skipped
    rather than raising, since stray non-conforming lines are common in
    real-world PDB files.
    """
    coords: List[Tuple[float, float, float]] = []
    for line in pdb_text.splitlines():
        if not line.startswith("ATOM"):
            continue
        try:
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue
        coords.append((x, y, z))
    return coords


def compute_protein_center_and_box(
    pdb_text: str,
    padding_fraction: float = DEFAULT_PADDING_FRACTION,
) -> Tuple[List[float], List[float]]:
    """Return ``(center, box_size)`` covering every ``ATOM`` in *pdb_text*.

    ``center`` is the bounding box's geometric midpoint per axis
    (``(min+max)/2``), not the atom centroid -- only the midpoint of the
    same bounding box the size is derived from guarantees the resulting box
    actually covers the whole structure. ``box_size`` is the bounding box's
    extent per axis, expanded by *padding_fraction* (default 10%). Both are
    rounded to 3 decimals, matching this codebase's existing box-value
    display convention.

    Raises ``ValueError`` if *pdb_text* has no ``ATOM`` records (empty,
    ligand-only, or malformed structure).
    """
    coords = parse_atom_coordinates(pdb_text)
    if not coords:
        raise ValueError(
            "No ATOM records found in the protein structure -- cannot "
            "compute a bounding box from an empty or malformed PDB."
        )

    xs, ys, zs = zip(*coords)
    mins = (min(xs), min(ys), min(zs))
    maxs = (max(xs), max(ys), max(zs))

    center = [round((mn + mx) / 2, 3) for mn, mx in zip(mins, maxs)]
    box_size = [round((mx - mn) * (1 + padding_fraction), 3) for mn, mx in zip(mins, maxs)]
    return center, box_size
