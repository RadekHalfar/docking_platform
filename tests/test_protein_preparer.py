"""
Unit tests for Protein Preparer service
"""
import pytest
import os
import tempfile
from pathlib import Path

# Check if RDKit is available
try:
    from rdkit import Chem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

# Skip all tests if RDKit not available
pytestmark = pytest.mark.skipif(not RDKIT_AVAILABLE, reason="RDKit not installed")

from services.protein_preparer import ProteinPreparer


# Test fixtures
@pytest.fixture
def temp_dir():
    """Create temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_protein_pdb(temp_dir):
    """Create a small sample protein PDB file for testing."""
    pdb_content = """REMARK   Test protein structure
ATOM      1  N   ALA A   1      10.000  20.000  30.000  1.00  0.00           N
ATOM      2  CA  ALA A   1      11.000  20.000  30.000  1.00  0.00           C
ATOM      3  C   ALA A   1      11.500  21.000  30.000  1.00  0.00           C
ATOM      4  O   ALA A   1      11.000  22.000  30.000  1.00  0.00           O
ATOM      5  CB  ALA A   1      11.500  19.000  30.000  1.00  0.00           C
ATOM      6  N   VAL A   2      12.500  21.000  30.000  1.00  0.00           N
ATOM      7  CA  VAL A   2      13.000  22.000  30.000  1.00  0.00           C
ATOM      8  C   VAL A   2      13.500  22.500  31.000  1.00  0.00           C
ATOM      9  O   VAL A   2      13.000  23.000  32.000  1.00  0.00           O
ATOM     10  CB  VAL A   2      14.000  21.500  29.000  1.00  0.00           C
HETATM   11  O   HOH A 101      15.000  25.000  35.000  1.00  0.00           O
HETATM   12  O   HOH A 102      16.000  26.000  36.000  1.00  0.00           O
END
"""
    pdb_path = os.path.join(temp_dir, "test_protein.pdb")
    with open(pdb_path, 'w') as f:
        f.write(pdb_content)
    return pdb_path


@pytest.fixture
def protein_with_ligand(temp_dir):
    """Create PDB with protein and ligand."""
    pdb_content = """REMARK   Protein with ligand
ATOM      1  N   ALA A   1      10.000  20.000  30.000  1.00  0.00           N
ATOM      2  CA  ALA A   1      11.000  20.000  30.000  1.00  0.00           C
ATOM      3  C   ALA A   1      11.500  21.000  30.000  1.00  0.00           C
ATOM      4  O   ALA A   1      11.000  22.000  30.000  1.00  0.00           O
HETATM    5  C1  LIG A 301      20.000  25.000  35.000  1.00  0.00           C
HETATM    6  C2  LIG A 301      21.000  25.000  35.000  1.00  0.00           C
HETATM    7  O   LIG A 301      21.500  26.000  35.000  1.00  0.00           O
HETATM    8  O   HOH A 401      30.000  30.000  40.000  1.00  0.00           O
HETATM    9  O   HOH A 402      31.000  31.000  41.000  1.00  0.00           O
END
"""
    pdb_path = os.path.join(temp_dir, "protein_ligand.pdb")
    with open(pdb_path, 'w') as f:
        f.write(pdb_content)
    return pdb_path


# Test ProteinPreparer class
class TestProteinPreparer:
    """Test ProteinPreparer functionality."""
    
    def test_initialization(self):
        """Test ProteinPreparer initialization."""
        preparer = ProteinPreparer()
        assert preparer is not None
    
    def test_initialization_with_cache_dir(self, temp_dir):
        """Test initialization with cache directory."""
        cache_dir = os.path.join(temp_dir, "cache")
        preparer = ProteinPreparer(cache_dir=cache_dir)
        assert preparer.cache_dir == cache_dir
        assert os.path.exists(cache_dir)
    
    def test_read_protein_pdb(self, sample_protein_pdb):
        """Test reading protein from PDB file."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        assert mol is not None
        assert mol.GetNumAtoms() == 12  # 10 protein atoms + 2 waters
    
    def test_read_protein_invalid_format(self, temp_dir):
        """Test reading protein with invalid format."""
        invalid_file = os.path.join(temp_dir, "test.xyz")
        with open(invalid_file, 'w') as f:
            f.write("invalid content")
        
        preparer = ProteinPreparer()
        with pytest.raises(ValueError, match="Unsupported file format"):
            preparer.read_protein(invalid_file)
    
    def test_read_protein_nonexistent_file(self):
        """Test reading non-existent protein file."""
        preparer = ProteinPreparer()
        with pytest.raises((ValueError, OSError)):
            preparer.read_protein("nonexistent.pdb")
    
    def test_clean_protein_remove_waters(self, sample_protein_pdb):
        """Test removing water molecules from protein."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        initial_atoms = mol.GetNumAtoms()
        
        cleaned_mol = preparer.clean_protein(mol, remove_waters=True)
        final_atoms = cleaned_mol.GetNumAtoms()
        
        assert final_atoms < initial_atoms  # Waters should be removed
        assert final_atoms == 10  # Only protein atoms remain
    
    def test_clean_protein_keep_waters(self, sample_protein_pdb):
        """Test keeping water molecules."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        initial_atoms = mol.GetNumAtoms()
        
        cleaned_mol = preparer.clean_protein(mol, remove_waters=False)
        final_atoms = cleaned_mol.GetNumAtoms()
        
        assert final_atoms == initial_atoms  # All atoms kept
    
    def test_clean_protein_remove_heteroatoms(self, protein_with_ligand):
        """Test removing heteroatoms (ligands)."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(protein_with_ligand)
        
        cleaned_mol = preparer.clean_protein(
            mol,
            remove_waters=True,
            remove_heteroatoms=True
        )
        
        # Should remove ligand (3 atoms) and waters (2 atoms)
        assert cleaned_mol.GetNumAtoms() == 4  # Only protein atoms
    
    def test_clean_protein_keep_specific_residues(self, protein_with_ligand):
        """Test keeping specific residues while removing others."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(protein_with_ligand)
        
        cleaned_mol = preparer.clean_protein(
            mol,
            remove_waters=True,
            remove_heteroatoms=True,
            keep_residues=['LIG']
        )
        
        # Should keep protein (4 atoms) + ligand (3 atoms), remove waters
        assert cleaned_mol.GetNumAtoms() == 7
    
    def test_prepare_protein_add_hydrogens(self, sample_protein_pdb):
        """Test adding hydrogens to protein."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        initial_atoms = mol.GetNumAtoms()
        
        prepared_mol = preparer.prepare_protein(
            mol,
            add_hydrogens=True,
            remove_waters=True
        )
        
        final_atoms = prepared_mol.GetNumAtoms()
        assert final_atoms > initial_atoms  # Hydrogens added
    
    def test_prepare_protein_no_hydrogens(self, sample_protein_pdb):
        """Test preparation without adding hydrogens."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        
        # Remove waters first to get consistent count
        mol = preparer.clean_protein(mol, remove_waters=True)
        initial_atoms = mol.GetNumAtoms()
        
        prepared_mol = preparer.prepare_protein(
            mol,
            add_hydrogens=False,
            remove_waters=False
        )
        
        final_atoms = prepared_mol.GetNumAtoms()
        assert final_atoms == initial_atoms  # No hydrogens added
    
    def test_save_as_pdb(self, sample_protein_pdb, temp_dir):
        """Test saving protein as PDB file."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        
        output_path = os.path.join(temp_dir, "output.pdb")
        result_path = preparer.save_as_pdb(mol, output_path)
        
        assert os.path.exists(result_path)
        assert result_path == output_path
        
        # Verify we can read it back
        mol2 = preparer.read_protein(result_path)
        assert mol2 is not None
    
    def test_prepare_protein_file(self, sample_protein_pdb, temp_dir):
        """Test preparing a complete protein file."""
        preparer = ProteinPreparer()
        output_path = os.path.join(temp_dir, "prepared.pdbqt")
        
        result = preparer.prepare_protein_file(
            protein_path=sample_protein_pdb,
            output_path=output_path,
            ph=7.4,
            add_hydrogens=True,
            remove_waters=True
        )
        
        assert result["id"] == "test_protein"
        assert result["input_path"] == sample_protein_pdb
        assert result["pdbqt_path"] == output_path
        assert "pdb_path" in result
        assert result["initial_atoms"] == 12
        assert result["final_atoms"] > 0
        # Note: final_atoms may be > initial_atoms if hydrogens added, < if waters removed
        # Just verify atoms_removed is calculated (can be negative if H added)
        assert "atoms_removed" in result
        assert "conversion_method" in result
        assert "checksum" in result
        
        # Verify files exist
        assert os.path.exists(result["pdb_path"])
        assert os.path.exists(result["pdbqt_path"])
    
    def test_prepare_proteins_batch(self, sample_protein_pdb, protein_with_ligand, temp_dir):
        """Test batch protein preparation."""
        preparer = ProteinPreparer()
        output_dir = os.path.join(temp_dir, "prepared")
        
        result = preparer.prepare_proteins(
            protein_paths=[sample_protein_pdb, protein_with_ligand],
            output_dir=output_dir,
            ph=7.4,
            add_hydrogens=False,
            remove_waters=True
        )
        
        assert result["status"] == "ok"
        assert result["total"] == 2
        assert result["successful"] == 2
        assert result["failed"] == 0
        assert len(result["prepared_proteins"]) == 2
        assert len(result["errors"]) == 0
        
        # Verify output files exist
        for protein_result in result["prepared_proteins"]:
            assert os.path.exists(protein_result["pdbqt_path"])
    
    def test_prepare_proteins_with_error(self, sample_protein_pdb, temp_dir):
        """Test batch preparation with one failing file."""
        preparer = ProteinPreparer()
        output_dir = os.path.join(temp_dir, "prepared")
        
        # Include a non-existent file
        result = preparer.prepare_proteins(
            protein_paths=[sample_protein_pdb, "nonexistent.pdb"],
            output_dir=output_dir
        )
        
        assert result["total"] == 2
        assert result["successful"] == 1
        assert result["failed"] == 1
        assert len(result["prepared_proteins"]) == 1
        assert len(result["errors"]) == 1
    
    def test_conversion_method_available(self, sample_protein_pdb, temp_dir):
        """Test that conversion method is reported correctly."""
        preparer = ProteinPreparer()
        output_path = os.path.join(temp_dir, "test.pdbqt")
        
        # Prepare protein file
        result = preparer.prepare_protein_file(
            protein_path=sample_protein_pdb,
            output_path=output_path
        )
        
        # Check that manual_rigid conversion method is used (default for receptors)
        assert os.path.exists(output_path)
        assert result["conversion_method"] == "manual_rigid"

    def test_convert_to_pdbqt_manual_includes_hetatm_and_skips_malformed(self, temp_dir):
        """convert_to_pdbqt_manual() writes both ATOM and HETATM records
        (unlike common.pdb_geometry.parse_atom_coordinates(), which is
        ATOM-only by design) and skips a malformed atom line without
        failing the whole conversion -- exercises the shared
        common.pdb_geometry.parse_pdb_atom_line() parser this method now
        uses (Review Stage R1, Phase 12)."""
        pdb_content = (
            "ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N\n"
            "HETATM    2  O   HOH A   2     100.000 100.000 100.000  1.00  0.00           O\n"
            "ATOM      3  X   AAA A   1                                                  \n"
            "END\n"
        )
        pdb_path = os.path.join(temp_dir, "mixed.pdb")
        output_path = os.path.join(temp_dir, "mixed.pdbqt")
        with open(pdb_path, "w") as f:
            f.write(pdb_content)

        preparer = ProteinPreparer()
        result = preparer.convert_to_pdbqt_manual(pdb_path, output_path)

        assert result["success"] is True
        assert result["atom_count"] == 2  # malformed 3rd line skipped

        with open(output_path) as f:
            lines = f.read().splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("ATOM      1")
        assert lines[1].startswith("ATOM      2")  # HETATM -> still written as "ATOM"

    def test_gasteiger_charges_assigned(self, sample_protein_pdb, temp_dir):
        """Test that Gasteiger charges are assigned."""
        preparer = ProteinPreparer()
        mol = preparer.read_protein(sample_protein_pdb)
        
        prepared_mol = preparer.prepare_protein(
            mol,
            add_hydrogens=False,
            remove_waters=True
        )
        
        # Check that charges are assigned
        has_charges = any(
            atom.HasProp('_GasteigerCharge')
            for atom in prepared_mol.GetAtoms()
        )
        assert has_charges
    
    def test_pdbqt_format_validation(self, sample_protein_pdb, temp_dir):
        """Test that output PDBQT file has correct format."""
        preparer = ProteinPreparer()
        output_path = os.path.join(temp_dir, "test.pdbqt")
        
        preparer.prepare_protein_file(
            protein_path=sample_protein_pdb,
            output_path=output_path
        )
        
        # Read PDBQT and verify format
        with open(output_path, 'r') as f:
            content = f.read()
        
        # Should have ATOM or HETATM records
        assert 'ATOM' in content or 'HETATM' in content
        
        # Should have atomic coordinates
        lines = content.split('\n')
        atom_lines = [l for l in lines if l.startswith('ATOM') or l.startswith('HETATM')]
        assert len(atom_lines) > 0
        
        # Each ATOM line should have charge and atom type columns
        for line in atom_lines[:5]:  # Check first 5
            assert len(line) >= 78
            # Should have charge column around position 70-76
            float(line[70:76])
            # Should have atom type at the end
            atom_type_part = line[77:].strip()
            assert len(atom_type_part) > 0


class TestProteinPreparerEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_protein_file(self, temp_dir):
        """Test handling of empty protein file."""
        empty_pdb = os.path.join(temp_dir, "empty.pdb")
        with open(empty_pdb, 'w') as f:
            f.write("END\n")
        
        preparer = ProteinPreparer()
        with pytest.raises(ValueError):
            preparer.read_protein(empty_pdb)
    
    def test_invalid_pdb_content(self, temp_dir):
        """Test handling of invalid PDB content."""
        invalid_pdb = os.path.join(temp_dir, "invalid.pdb")
        with open(invalid_pdb, 'w') as f:
            f.write("INVALID CONTENT\nNOT A PDB FILE\n")
        
        preparer = ProteinPreparer()
        with pytest.raises(ValueError):
            preparer.read_protein(invalid_pdb)
    
    def test_protein_with_only_waters(self, temp_dir):
        """Test protein file containing only water molecules."""
        water_only_pdb = os.path.join(temp_dir, "waters.pdb")
        pdb_content = """HETATM    1  O   HOH A   1      10.000  20.000  30.000  1.00  0.00           O
HETATM    2  O   HOH A   2      11.000  21.000  31.000  1.00  0.00           O
END
"""
        with open(water_only_pdb, 'w') as f:
            f.write(pdb_content)
        
        preparer = ProteinPreparer()
        mol = preparer.read_protein(water_only_pdb)
        
        # Remove waters should result in empty molecule
        cleaned_mol = preparer.clean_protein(mol, remove_waters=True)
        assert cleaned_mol.GetNumAtoms() == 0


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
