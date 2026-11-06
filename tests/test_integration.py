"""
Download-then-prepare workflow across two service classes, in process.

The RCSB request is mocked and no service runs, so this is part of the hermetic
suite. tests/test_docker_integration.py covers the real running services.
"""
import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

# Check if RDKit is available
try:
    from rdkit import Chem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not RDKIT_AVAILABLE, reason="RDKit not installed")

from services.rcsb_downloader import RCSBDownloader
from services.ligand_preparer import LigandPreparer


def test_download_and_prepare_workflow(tmp_path):
    """
    Integration test: Download ligand from RCSB and prepare for docking.
    
    This test demonstrates the complete workflow:
    1. Download ligand from RCSB
    2. Prepare ligand for docking (PDBQT format)
    """
    # Setup directories
    download_dir = tmp_path / "downloads"
    prepare_dir = tmp_path / "prepared"
    
    # Mock the download to avoid network calls
    sample_sdf = """benzene
  Mrv2311 12202315362D          

  6  6  0  0  0  0            999 V2000
    0.0000    0.8250    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.7145    0.4125    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.7145   -0.4125    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -0.8250    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.7145   -0.4125    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.7145    0.4125    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
  2  3  2  0  0  0  0
  3  4  1  0  0  0  0
  4  5  2  0  0  0  0
  5  6  1  0  0  0  0
  6  1  2  0  0  0  0
M  END
$$$$
"""
    
    # Step 1: Download ligand (mocked)
    downloader = RCSBDownloader()
    
    with patch('services.rcsb_downloader.requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_sdf.encode()
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        download_result = downloader.download_ligand(
            "BNZ",  # Mock ligand ID
            str(download_dir),
            "sdf"
        )
    
    assert os.path.exists(download_result["file_path"])
    print(f"✓ Downloaded ligand to: {download_result['file_path']}")
    
    # Step 2: Prepare ligand
    preparer = LigandPreparer()
    
    prepare_result = preparer.prepare_ligand(
        download_result["file_path"],
        str(prepare_dir),
        ph=7.4,
        add_hydrogens=True,
        optimize=True
    )
    
    assert os.path.exists(prepare_result["pdbqt_path"])
    assert prepare_result["pdbqt_path"].endswith(".pdbqt")
    print(f"✓ Prepared ligand to: {prepare_result['pdbqt_path']}")
    
    # Verify PDBQT content
    with open(prepare_result["pdbqt_path"], 'r') as f:
        pdbqt_content = f.read()
        assert "ATOM" in pdbqt_content or "HETATM" in pdbqt_content
        assert "TORSDOF" in pdbqt_content
    
    print(f"✓ PDBQT file is valid")
    print(f"✓ Rotatable bonds: {prepare_result['rotatable_bonds']}")
    print(f"✓ Conversion method: {prepare_result['conversion_method']}")
    
    # Summary
    print("\n" + "="*60)
    print("Integration Test Summary")
    print("="*60)
    print(f"Workflow: RCSB Download → Ligand Preparation")
    print(f"Input: Mock BNZ ligand (benzene)")
    print(f"Output: {prepare_result['pdbqt_path']}")
    print(f"Status: SUCCESS ✓")
    print("="*60)


def test_batch_prepare_workflow(tmp_path):
    """
    Integration test: Prepare multiple ligands in batch.
    """
    # Create multiple test molecules
    test_mols = {
        "methane": "C",
        "ethanol": "CCO",
        "benzene": "c1ccccc1"
    }
    
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    
    # Create SDF files
    input_files = []
    for name, smiles in test_mols.items():
        mol = Chem.MolFromSmiles(smiles)
        sdf_path = input_dir / f"{name}.sdf"
        writer = Chem.SDWriter(str(sdf_path))
        writer.write(mol)
        writer.close()
        input_files.append(str(sdf_path))
    
    # Prepare all ligands
    preparer = LigandPreparer()
    result = preparer.prepare_ligands(
        input_files,
        str(output_dir),
        ph=7.4
    )
    
    assert result["status"] == "ok"
    assert result["total"] == 3
    assert result["successful"] == 3
    assert result["failed"] == 0
    
    # Verify all PDBQT files exist
    for ligand_info in result["prepared_ligands"]:
        assert os.path.exists(ligand_info["pdbqt_path"])
        print(f"✓ {ligand_info['id']}: {ligand_info['rotatable_bonds']} rotatable bonds")
    
    print(f"\n✓ Successfully prepared {result['successful']}/{result['total']} ligands")


if __name__ == "__main__":
    """
    Run integration tests manually.
    """
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        print("Running Integration Tests")
        print("="*60)
        
        try:
            test_download_and_prepare_workflow(tmp_path)
            print("\n")
            test_batch_prepare_workflow(tmp_path)
            print("\nAll integration tests passed! ✓")
        except Exception as e:
            print(f"\nTest failed: {e}")
            import traceback
            traceback.print_exc()
