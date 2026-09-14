"""Protein bounding-box geometry computation from raw PDB text."""
from pathlib import Path

import pytest

from common.pdb_geometry import compute_protein_center_and_box, parse_atom_coordinates


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def mini_protein_pdb(fixtures_dir):
    pdb_file = fixtures_dir / "mini_protein.pdb"
    if not pdb_file.exists():
        pytest.skip("Fixture file not available")
    return pdb_file.read_text()


# tests/fixtures/mini_protein.pdb has 4 ATOM records at
# (1,2,3), (11,2,3), (1,22,3), (1,2,33) -> min=(1,2,3), max=(11,22,33),
# plus one HETATM at (100,100,100) that must NOT affect the result.


def test_parse_atom_coordinates_excludes_hetatm(mini_protein_pdb):
    coords = parse_atom_coordinates(mini_protein_pdb)
    assert coords == [
        (1.000, 2.000, 3.000),
        (11.000, 2.000, 3.000),
        (1.000, 22.000, 3.000),
        (1.000, 2.000, 33.000),
    ]


def test_parse_atom_coordinates_skips_unparseable_lines():
    text = "ATOM  garbage line too short\nATOM  " + " " * 100 + "\n"
    assert parse_atom_coordinates(text) == []


def test_compute_protein_center_and_box_default_padding(mini_protein_pdb):
    center, box_size = compute_protein_center_and_box(mini_protein_pdb)
    assert center == [6.0, 12.0, 18.0]
    assert box_size == [11.0, 22.0, 33.0]


def test_compute_protein_center_and_box_custom_padding(mini_protein_pdb):
    center, box_size = compute_protein_center_and_box(mini_protein_pdb, padding_fraction=0.20)
    assert center == [6.0, 12.0, 18.0]
    assert box_size == [12.0, 24.0, 36.0]


def test_compute_protein_center_and_box_zero_padding(mini_protein_pdb):
    _, box_size = compute_protein_center_and_box(mini_protein_pdb, padding_fraction=0.0)
    assert box_size == [10.0, 20.0, 30.0]


def test_compute_protein_center_and_box_empty_raises():
    with pytest.raises(ValueError, match="No ATOM records"):
        compute_protein_center_and_box("HEADER    ONLY A HETATM RECORD\nEND\n")


def test_compute_protein_center_and_box_hetatm_only_raises():
    hetatm_line = (
        "HETATM    1  O   HOH A   1       5.000   5.000   5.000  1.00  0.00           O"
    )
    with pytest.raises(ValueError, match="No ATOM records"):
        compute_protein_center_and_box(hetatm_line + "\nEND\n")
