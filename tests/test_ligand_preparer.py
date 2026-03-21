"""
Unit tests for Ligand Preparer service.
"""
import pytest
import os
from pathlib import Path
from unittest.mock import patch, Mock

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
