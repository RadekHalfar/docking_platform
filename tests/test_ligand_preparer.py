"""
Unit tests for Ligand Preparer service.
"""
import sys
import pytest
import os
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

# Check if RDKit is available
try:
    from rdkit import Chem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

# Skip all tests if RDKit not available
pytestmark = pytest.mark.skipif(not RDKIT_AVAILABLE, reason="RDKit not installed")

from services.ligand_preparer import LigandPreparer


@pytest.fixture
def preparer(tmp_path):
    """Create LigandPreparer instance with temp cache."""
    cache_dir = str(tmp_path / "cache")
    return LigandPreparer(cache_dir=cache_dir)


@pytest.fixture
def fixtures_dir():
    """Get path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


def test_read_ligand_sdf(preparer, fixtures_dir):
    """Test reading ligand from SDF file."""
    sdf_file = fixtures_dir / "benzene.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")
    
    mol = preparer.read_ligand(str(sdf_file))
    assert mol is not None
    assert mol.GetNumAtoms() == 6  # Benzene has 6 carbons


def test_read_ligand_unsupported_format(preparer, tmp_path):
    """Test reading ligand with unsupported format."""
    fake_file = tmp_path / "test.xyz"
    fake_file.write_text("fake content")
    
    with pytest.raises(ValueError, match="Unsupported file format"):
        preparer.read_ligand(str(fake_file))


def test_read_ligand_invalid_file(preparer, tmp_path):
    """Test reading invalid SDF file."""
    bad_sdf = tmp_path / "bad.sdf"
    bad_sdf.write_text("not a valid sdf")
    
    with pytest.raises(ValueError, match="No valid molecules|Failed to read"):
        preparer.read_ligand(str(bad_sdf))


def test_prepare_molecule_basic(preparer):
    """Test basic molecule preparation."""
    # Create a simple molecule (methane)
    mol = Chem.MolFromSmiles("C")
    
    prepared = preparer.prepare_molecule(mol, ph=7.4, add_hydrogens=True, generate_3d=True)
    
    assert prepared is not None
    assert prepared.GetNumAtoms() == 5  # 1 C + 4 H
    assert prepared.GetNumConformers() == 1  # Should have 3D coords


def test_prepare_molecule_no_3d(preparer):
    """Test molecule preparation without 3D generation."""
    mol = Chem.MolFromSmiles("C")
    
    prepared = preparer.prepare_molecule(mol, generate_3d=False)
    
    assert prepared is not None
    # May or may not have conformers depending on input


def test_prepare_molecule_with_existing_3d(preparer):
    """Test preparation with existing 3D coordinates."""
    mol = Chem.MolFromSmiles("CCO")
    from rdkit.Chem import AllChem
    AllChem.EmbedMolecule(mol)
    
    assert mol.GetNumConformers() == 1
    
    prepared = preparer.prepare_molecule(mol, generate_3d=False)
    assert prepared.GetNumConformers() == 1


def test_save_as_pdb(preparer, tmp_path):
    """Test saving molecule as PDB."""
    mol = Chem.MolFromSmiles("CCO")
    from rdkit.Chem import AllChem
    AllChem.EmbedMolecule(mol)
    mol = Chem.AddHs(mol, addCoords=True)
    
    pdb_file = tmp_path / "test.pdb"
    result = preparer.save_as_pdb(mol, str(pdb_file))
    
    assert os.path.exists(result)
    assert pdb_file.read_text().startswith("HETATM") or pdb_file.read_text().startswith("ATOM")


def test_convert_to_pdbqt_manual(preparer, tmp_path):
    """Test manual PDBQT conversion."""
    mol = Chem.MolFromSmiles("C")
    from rdkit.Chem import AllChem
    AllChem.EmbedMolecule(mol)
    mol = Chem.AddHs(mol, addCoords=True)
    
    pdb_file = tmp_path / "test.pdb"
    preparer.save_as_pdb(mol, str(pdb_file))
    
    pdbqt_file = tmp_path / "test.pdbqt"
    result = preparer.convert_to_pdbqt_manual(str(pdb_file), str(pdbqt_file))
    
    assert os.path.exists(pdbqt_file)
    assert result["method"] == "manual"
    assert result["rotatable_bonds"] >= 0
    
    # Check PDBQT content
    content = pdbqt_file.read_text()
    assert "TORSDOF" in content


def test_convert_to_pdbqt_obabel_falls_back_to_manual_on_nonzero_exit(preparer, tmp_path):
    """obabel missing (FileNotFoundError) and obabel running but failing
    (non-zero exit) are the same underlying situation -- neither produced a
    usable PDBQT -- so both must fall back to the manual converter the same
    way, not just the missing-binary case."""
    mol = Chem.MolFromSmiles("C")
    from rdkit.Chem import AllChem
    AllChem.EmbedMolecule(mol)
    mol = Chem.AddHs(mol, addCoords=True)

    pdb_file = tmp_path / "test.pdb"
    preparer.save_as_pdb(mol, str(pdb_file))
    pdbqt_file = tmp_path / "test.pdbqt"

    fake_result = Mock(returncode=1, stderr="obabel: conversion error")
    with patch("services.ligand_preparer.subprocess.run", return_value=fake_result):
        result = preparer.convert_to_pdbqt_obabel(str(pdb_file), str(pdbqt_file))

    assert result["method"] == "manual"
    assert os.path.exists(pdbqt_file)


def test_prepare_ligand_complete(preparer, fixtures_dir, tmp_path):
    """Test complete ligand preparation workflow."""
    sdf_file = fixtures_dir / "benzene.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")
    
    output_dir = tmp_path / "prepared"
    
    result = preparer.prepare_ligand(
        str(sdf_file),
        str(output_dir),
        ph=7.4
    )
    
    assert result["id"] == "benzene"
    assert "pdbqt_path" in result
    assert os.path.exists(result["pdbqt_path"])
    assert result["pdbqt_path"].endswith(".pdbqt")
    assert "checksum" in result
    assert "rotatable_bonds" in result
    assert result["conversion_method"] in ["meeko", "openbabel", "manual"]


def test_prepare_ligand_with_water(preparer, fixtures_dir, tmp_path):
    """Test preparing a simple molecule (water)."""
    water_file = fixtures_dir / "water.sdf"
    if not water_file.exists():
        pytest.skip("Fixture file not available")
    
    output_dir = tmp_path / "prepared_water"
    
    result = preparer.prepare_ligand(
        str(water_file),
        str(output_dir)
    )
    
    assert result["id"] == "water"
    assert os.path.exists(result["pdbqt_path"])
    assert result["rotatable_bonds"] == 0  # Water has no rotatable bonds


def test_prepare_ligands_multiple(preparer, fixtures_dir, tmp_path):
    """Test preparing multiple ligands."""
    benzene_file = fixtures_dir / "benzene.sdf"
    water_file = fixtures_dir / "water.sdf"
    
    if not (benzene_file.exists() and water_file.exists()):
        pytest.skip("Fixture files not available")
    
    output_dir = tmp_path / "multi_prep"
    
    result = preparer.prepare_ligands(
        [str(benzene_file), str(water_file)],
        str(output_dir),
        ph=7.4
    )
    
    assert result["status"] == "ok"
    assert result["total"] == 2
    assert result["successful"] == 2
    assert result["failed"] == 0
    assert len(result["prepared_ligands"]) == 2
    
    # Check metadata was saved
    metadata_file = output_dir / "preparation_meta.json"
    assert metadata_file.exists()


def test_prepare_ligands_with_failure(preparer, tmp_path):
    """Test preparing ligands with some failures."""
    # Create a good file
    good_sdf = tmp_path / "good.sdf"
    mol = Chem.MolFromSmiles("C")
    writer = Chem.SDWriter(str(good_sdf))
    writer.write(mol)
    writer.close()
    
    # Create a bad file
    bad_sdf = tmp_path / "bad.sdf"
    bad_sdf.write_text("invalid sdf content")
    
    output_dir = tmp_path / "mixed_prep"
    
    result = preparer.prepare_ligands(
        [str(good_sdf), str(bad_sdf)],
        str(output_dir)
    )
    
    assert result["total"] == 2
    assert result["successful"] == 1
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    assert result["status"] == "ok"  # Partial success


def test_prepare_ligands_all_fail(preparer, tmp_path):
    """Test preparing ligands when all fail."""
    bad_sdf = tmp_path / "bad.sdf"
    bad_sdf.write_text("invalid")
    
    output_dir = tmp_path / "fail_prep"
    
    result = preparer.prepare_ligands(
        [str(bad_sdf)],
        str(output_dir)
    )
    
    assert result["status"] == "failed"
    assert result["successful"] == 0
    assert result["failed"] == 1


def test_prepare_ligands_from_file_single_molecule_sdf_unchanged_naming(preparer, fixtures_dir, tmp_path):
    """A single-molecule SDF through prepare_ligands_from_file() keeps the
    same id/filenames as prepare_ligand() — no "_1" suffix added."""
    sdf_file = fixtures_dir / "benzene.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")

    output_dir = tmp_path / "single_via_multi"
    results, errors = preparer.prepare_ligands_from_file(str(sdf_file), str(output_dir))

    assert errors == []
    assert len(results) == 1
    assert results[0]["id"] == "benzene"
    assert os.path.exists(results[0]["pdbqt_path"])


def test_prepare_ligands_from_file_multi_molecule_sdf(preparer, fixtures_dir, tmp_path):
    """A multi-molecule SDF expands into one prepared ligand per molecule,
    each with a unique id and output files."""
    sdf_file = fixtures_dir / "multi_ligand.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")

    output_dir = tmp_path / "multi_expand"
    results, errors = preparer.prepare_ligands_from_file(str(sdf_file), str(output_dir))

    assert errors == []
    assert len(results) == 3

    ids = [r["id"] for r in results]
    assert ids == ["multi_ligand_1", "multi_ligand_2", "multi_ligand_3"]

    pdbqt_paths = [r["pdbqt_path"] for r in results]
    assert len(set(pdbqt_paths)) == 3, "prepared ligands must have unique output files"
    for path in pdbqt_paths:
        assert os.path.exists(path)

    for i, r in enumerate(results, start=1):
        assert r["molecule_index"] == i
        assert r["molecule_count"] == 3
        assert r["source_file"] == str(sdf_file)

    # First record's title line is "benzene"; water.sdf's title line is
    # blank (name lives in an SD tag we don't read), so it falls back to None.
    assert results[0]["sdf_title"] == "benzene"
    assert results[1]["sdf_title"] is None
    assert results[2]["sdf_title"] == "methane"


def test_prepare_ligands_from_file_skips_unparseable_records(preparer, fixtures_dir, tmp_path):
    """A malformed record inside an otherwise-valid multi-molecule SDF is
    silently skipped during parsing (pre-existing SDMolSupplier behavior),
    the same way a single-molecule bad SDF is already tolerated elsewhere."""
    sdf_file = fixtures_dir / "multi_ligand_with_unparseable.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")

    output_dir = tmp_path / "multi_with_garbage"
    results, errors = preparer.prepare_ligands_from_file(str(sdf_file), str(output_dir))

    assert errors == []
    assert len(results) == 2
    assert [r["id"] for r in results] == [
        "multi_ligand_with_unparseable_1",
        "multi_ligand_with_unparseable_2",
    ]


def test_prepare_ligands_from_file_partial_failure(preparer, fixtures_dir, tmp_path):
    """If one molecule fails during preparation, the others still succeed
    and the failure is reported with its position in the file."""
    sdf_file = fixtures_dir / "multi_ligand.sdf"
    if not sdf_file.exists():
        pytest.skip("Fixture file not available")

    output_dir = tmp_path / "multi_partial_fail"

    with patch.object(preparer, "_prepare_mol") as mock_prepare_mol:
        mock_prepare_mol.side_effect = [
            {"id": "multi_ligand_1"},
            RuntimeError("simulated preparation failure"),
            {"id": "multi_ligand_3"},
        ]
        results, errors = preparer.prepare_ligands_from_file(str(sdf_file), str(output_dir))

    assert len(results) == 2
    assert [r["id"] for r in results] == ["multi_ligand_1", "multi_ligand_3"]
    assert len(errors) == 1
    assert "molecule 2/3" in errors[0]["ligand"]
    assert "simulated preparation failure" in errors[0]["error"]


def test_prepare_ligands_multiple_with_multi_molecule_sdf(preparer, fixtures_dir, tmp_path):
    """The MCP-facing batch method expands multi-molecule SDFs too, while
    'total' keeps counting input files and 'total_ligands' reflects the
    post-expansion molecule count."""
    multi_file = fixtures_dir / "multi_ligand.sdf"
    benzene_file = fixtures_dir / "benzene.sdf"
    if not (multi_file.exists() and benzene_file.exists()):
        pytest.skip("Fixture files not available")

    output_dir = tmp_path / "batch_with_expansion"
    result = preparer.prepare_ligands(
        [str(multi_file), str(benzene_file)],
        str(output_dir),
    )

    assert result["status"] == "ok"
    assert result["total"] == 2  # input files
    assert result["total_ligands"] == 4  # 3 from multi_ligand.sdf + 1 from benzene.sdf
    assert result["successful"] == 4
    assert result["failed"] == 0
    assert len(result["prepared_ligands"]) == 4

    pdbqt_paths = [r["pdbqt_path"] for r in result["prepared_ligands"]]
    assert len(set(pdbqt_paths)) == 4, "no two prepared ligands may share an output file"


def test_count_rotatable_bonds_from_pdbqt(preparer, tmp_path):
    """Test extracting TORSDOF from PDBQT."""
    pdbqt_file = tmp_path / "test.pdbqt"
    pdbqt_file.write_text("ATOM      1  C   LIG A   1\nTORSDOF 5\n")
    
    count = preparer._count_rotatable_bonds_from_pdbqt(str(pdbqt_file))
    assert count == 5


def test_prepare_molecule_optimization(preparer):
    """Test molecule optimization."""
    mol = Chem.MolFromSmiles("CCCC")  # Butane - has rotatable bonds
    
    prepared = preparer.prepare_molecule(mol, optimize=True)
    
    assert prepared is not None
    assert prepared.GetNumConformers() == 1


def test_prepare_molecule_no_optimization(preparer):
    """Test molecule without optimization."""
    mol = Chem.MolFromSmiles("C")
    
    prepared = preparer.prepare_molecule(mol, optimize=False, generate_3d=True)
    
    assert prepared is not None


@pytest.mark.parametrize("ph", [5.0, 7.4, 9.0])
def test_prepare_molecule_different_ph(preparer, ph):
    """Test preparation at different pH values."""
    mol = Chem.MolFromSmiles("CC(=O)O")  # Acetic acid

    prepared = preparer.prepare_molecule(mol, ph=ph)

    assert prepared is not None
    # Note: Current implementation doesn't actually change protonation by pH
    # For full pH-dependent protonation, tools like Dimorphite-DL would be needed


# ---------------------------------------------------------------------------
# Meeko conversion tests (A1) — meeko module is injected via sys.modules
# ---------------------------------------------------------------------------

def _make_mock_meeko(rotatable_bonds=None):
    """Build a minimal mock meeko module for convert_to_pdbqt_meeko tests."""
    mock_meeko = MagicMock()
    mock_setup = MagicMock()
    if rotatable_bonds is not None:
        mock_setup.rotatable_bonds = rotatable_bonds
    else:
        mock_setup.rotatable_bonds = []
    mock_meeko.MoleculePreparation.return_value.prepare.return_value = [mock_setup]
    mock_meeko.PDBQTWriterLegacy.write_string.return_value = "REMARK MEEKO OUTPUT\nATOM...\n"
    return mock_meeko


def _minimal_pdb(path: Path) -> None:
    path.write_text(
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C\nEND\n"
    )


def test_convert_to_pdbqt_meeko_success(preparer, tmp_path):
    """Happy path: meeko path writes PDBQT and reports method='meeko'."""
    pdb_path = tmp_path / "lig.pdb"
    _minimal_pdb(pdb_path)
    output_path = str(tmp_path / "lig.pdbqt")

    with patch.dict(sys.modules, {"meeko": _make_mock_meeko(rotatable_bonds=[])}):
        result = preparer.convert_to_pdbqt_meeko(str(pdb_path), output_path)

    assert result["method"] == "meeko"
    assert result["rotatable_bonds"] == 0
    assert os.path.exists(output_path)


def test_convert_to_pdbqt_meeko_reports_rotatable_bonds(preparer, tmp_path):
    """Rotatable bond count from setup should appear in the result dict."""
    pdb_path = tmp_path / "lig.pdb"
    _minimal_pdb(pdb_path)
    output_path = str(tmp_path / "lig.pdbqt")

    with patch.dict(sys.modules, {"meeko": _make_mock_meeko(rotatable_bonds=[1, 2, 3])}):
        result = preparer.convert_to_pdbqt_meeko(str(pdb_path), output_path)

    assert result["rotatable_bonds"] == 3


def test_convert_to_pdbqt_meeko_rotatable_bonds_fallback_on_error(preparer, tmp_path):
    """If len(setup.rotatable_bonds) raises, rotatable_bonds should default to 0."""
    pdb_path = tmp_path / "lig.pdb"
    _minimal_pdb(pdb_path)
    output_path = str(tmp_path / "lig.pdbqt")

    mock_meeko = _make_mock_meeko()
    setup = mock_meeko.MoleculePreparation.return_value.prepare.return_value[0]
    # Make len() raise — the except Exception: path should catch this and set rotatable_bonds=0
    setup.rotatable_bonds = property(lambda self: (_ for _ in ()).throw(AttributeError("no bonds")))

    with patch.dict(sys.modules, {"meeko": mock_meeko}):
        result = preparer.convert_to_pdbqt_meeko(str(pdb_path), output_path)

    assert result["rotatable_bonds"] == 0


def test_convert_to_pdbqt_meeko_falls_back_to_obabel_on_import_error(preparer, tmp_path):
    """ImportError from meeko should trigger the obabel fallback path."""
    pdb_path = tmp_path / "lig.pdb"
    _minimal_pdb(pdb_path)
    output_path = str(tmp_path / "lig.pdbqt")

    with patch.dict(sys.modules, {"meeko": None}):
        with patch.object(preparer, "convert_to_pdbqt_obabel", return_value={"method": "obabel"}) as mock_ob:
            result = preparer.convert_to_pdbqt_meeko(str(pdb_path), output_path)

    mock_ob.assert_called_once_with(str(pdb_path), output_path)
    assert result["method"] == "obabel"
