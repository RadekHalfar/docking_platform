"""Shared PDB fixed-column atom parsing, and protein bounding-box geometry
derived from it.

The atom-line parser (`parse_pdb_atom_line`) is the single definition of
"how to read one ATOM/HETATM line" for this codebase -- used both by the
geometry computation below (`center`/`box_size` auto-fill: "set box center
as protein center" / "look across all protein" chat commands, GUI:
`ui/core/chat.py`, CLI: `nlp_orchestrator.py`, both routed through
`services/llm_orchestrator/workflow_llm_parser.py::detect_protein_geometry_command()`)
and by `services/protein_preparer/__init__.py`'s manual-rigid-receptor PDBQT
fallback, so the column parsing exists once. The two callers still differ in
which record types they use (`ATOM` only here vs. `ATOM`+`HETATM` there) and
which fields they need -- that's each caller's own choice, not a difference
in how a PDB atom line is parsed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

DEFAULT_PADDING_FRACTION = 0.10


@dataclass(frozen=True)
class PdbAtomRecord:
    """One parsed ``ATOM``/``HETATM`` line's fields (fixed-column PDB format)."""

    record_type: str  # "ATOM" or "HETATM"
    atom_name: str
    res_name: str
    chain: str
    res_num: str
    x: float
    y: float
    z: float
    element: str


def parse_pdb_atom_line(line: str) -> Optional[PdbAtomRecord]:
    """Parse one ``ATOM``/``HETATM`` line, or return ``None``.

    Fixed-column PDB parsing (1-indexed per the PDB format spec): atom name
    (13-16), residue name (18-20), chain (22), residue number (23-26), x/y/z
    (31-38/39-46/47-54), element symbol (77-78, falling back to the atom
    name's first character on a short line). Returns ``None`` for a line
    that isn't an ``ATOM``/``HETATM`` record, or one that starts with that
    prefix but doesn't parse cleanly -- stray non-conforming lines are
    common in real-world PDB files, so callers skip rather than raise.
    """
    if not line.startswith(("ATOM", "HETATM")):
        return None
    record_type = "ATOM" if line.startswith("ATOM") else "HETATM"
    try:
        atom_name = line[12:16].strip()
        res_name = line[17:20].strip()
        chain = line[21]
        res_num = line[22:26].strip()
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
        element = line[76:78].strip() if len(line) > 77 else atom_name[0]
    except (ValueError, IndexError):
        return None
    return PdbAtomRecord(record_type, atom_name, res_name, chain, res_num, x, y, z, element)


def parse_atom_coordinates(pdb_text: str) -> List[Tuple[float, float, float]]:
    """Extract (x, y, z) from every ``ATOM`` record in *pdb_text*.

    ``HETATM`` records (waters, cofactors, ions) are deliberately excluded --
    this is what makes the result "protein" geometry rather than "whatever
    is left in the file" geometry, independent of the
    `remove_waters`/`remove_heteroatoms` settings used to prepare it.
    """
    coords: List[Tuple[float, float, float]] = []
    for line in pdb_text.splitlines():
        record = parse_pdb_atom_line(line)
        if record is not None and record.record_type == "ATOM":
            coords.append((record.x, record.y, record.z))
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
