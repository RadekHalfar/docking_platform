"""
Unit tests for RCSB Downloader service.
"""
import pytest
import os
import json
import requests
from unittest.mock import patch, Mock
from services.rcsb_downloader import RCSBDownloader


@pytest.fixture
def downloader(tmp_path):
    """Create RCSBDownloader instance with temp cache."""
    cache_dir = str(tmp_path / "cache")
    return RCSBDownloader(cache_dir=cache_dir)


@pytest.fixture
def sample_pdb_content():
    """Sample PDB file content."""
    return b"HEADER    TEST PROTEIN\nATOM      1  CA  ALA A   1"


@pytest.fixture
def sample_sdf_content():
    """Sample SDF file content."""
    return b"""
  Mrv0541 02231512123D          

  6  5  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
"""


def test_download_protein_success(downloader, tmp_path, sample_pdb_content):
    """Test successful protein download."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "downloads")
        result = downloader.download_protein("1ABC", save_dir, "pdb")
        
        assert result["file_path"].endswith("1ABC.pdb")
        assert os.path.exists(result["file_path"])
        assert "checksum" in result
        assert "source_url" in result
        assert "1ABC.pdb" in result["source_url"]


def test_download_protein_invalid_id(downloader, tmp_path):
    """Test protein download with invalid PDB ID."""
    with pytest.raises(ValueError, match="Invalid PDB ID"):
        downloader.download_protein("INVALID", str(tmp_path))


def test_download_protein_unsupported_format(downloader, tmp_path):
    """Test protein download with unsupported format."""
    with pytest.raises(ValueError, match="Unsupported format"):
        downloader.download_protein("1ABC", str(tmp_path), "xyz")


def test_download_protein_network_error(downloader, tmp_path):
    """Test protein download with network error."""
    import requests
    with patch('services.rcsb_downloader.requests.get') as mock_get:
        mock_get.side_effect = requests.RequestException("Network error")
        
        with pytest.raises(RuntimeError, match="Failed to download"):
            downloader.download_protein("1ABC", str(tmp_path))


def test_download_ligand_success(downloader, tmp_path, sample_sdf_content):
    """Test successful ligand download."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_sdf_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "ligands")
        result = downloader.download_ligand("ATP", save_dir, "sdf")
        
        assert result["file_path"].endswith("ATP.sdf")
        assert os.path.exists(result["file_path"])
        assert "checksum" in result
        assert "ATP_ideal.sdf" in result["source_url"]


def test_download_ligand_unsupported_format(downloader, tmp_path):
    """Test ligand download with unsupported format."""
    with pytest.raises(ValueError, match="Unsupported ligand format"):
        downloader.download_ligand("ATP", str(tmp_path), "xyz")


def test_download_structures_complete(downloader, tmp_path, sample_pdb_content, sample_sdf_content):
    """Test complete download of protein and ligands."""
    with patch('requests.get') as mock_get:
        # Mock responses for protein and ligands
        def mock_response_factory(url, timeout=None):
            mock_resp = Mock()
            if ".pdb" in url:
                mock_resp.content = sample_pdb_content
            else:
                mock_resp.content = sample_sdf_content
            mock_resp.raise_for_status = Mock()
            return mock_resp
        
        mock_get.side_effect = mock_response_factory
        
        save_dir = str(tmp_path / "complete")
        result = downloader.download_structures(
            protein_id="1ABC",
            ligand_ids=["ATP", "HEM"],
            formats={"protein": "pdb", "ligand": "sdf"},
            save_dir=save_dir
        )
        
        assert result["status"] == "ok"
        assert result["protein_path"].endswith("1ABC.pdb")
        assert len(result["ligand_paths"]) == 2
        assert os.path.exists(result["protein_path"])
        
        # Check metadata was saved
        metadata_file = os.path.join(save_dir, "1ABC_download_meta.json")
        assert os.path.exists(metadata_file)
        
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
            assert metadata["status"] == "ok"


def test_download_structures_protein_only(downloader, tmp_path, sample_pdb_content):
    """Test download with protein only (no ligands)."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "protein_only")
        result = downloader.download_structures(
            protein_id="2XYZ",
            ligand_ids=[],
            save_dir=save_dir
        )
        
        assert result["status"] == "ok"
        assert result["protein_path"].endswith("2XYZ.pdb")
        assert len(result["ligand_paths"]) == 0


def test_download_structures_caching(downloader, tmp_path, sample_pdb_content):
    """Test that caching works for repeated downloads."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir1 = str(tmp_path / "download1")
        save_dir2 = str(tmp_path / "download2")
        
        # First download
        result1 = downloader.download_protein("1ABC", save_dir1, "pdb")
        assert result1["cached"] is False
        assert mock_get.call_count == 1
        
        # Second download should use cache
        result2 = downloader.download_protein("1ABC", save_dir2, "pdb")
        assert result2["cached"] is True
        assert mock_get.call_count == 1  # Should not make another request


def test_download_structures_partial_ligand_failure(downloader, tmp_path, sample_pdb_content, sample_sdf_content):
    """Test that protein download succeeds even if some ligands fail."""
    call_count = [0]
    
    def mock_response_factory(url, timeout=None):
        mock_resp = Mock()
        call_count[0] += 1
        
        if ".pdb" in url:
            mock_resp.content = sample_pdb_content
            mock_resp.raise_for_status = Mock()
        elif "ATP" in url:
            mock_resp.content = sample_sdf_content
            mock_resp.raise_for_status = Mock()
        else:
            # Simulate failure for second ligand
            mock_resp.raise_for_status = Mock(side_effect=requests.HTTPError("Not found"))
        
        return mock_resp
    
    with patch('requests.get') as mock_get:
        mock_get.side_effect = mock_response_factory
        
        save_dir = str(tmp_path / "partial")
        result = downloader.download_structures(
            protein_id="1ABC",
            ligand_ids=["ATP", "BADLIG"],
            save_dir=save_dir
        )
        
        # Protein should succeed
        assert result["status"] == "ok"
        assert os.path.exists(result["protein_path"])
        
        # Only one ligand should be downloaded
        assert len(result["ligand_paths"]) == 1
