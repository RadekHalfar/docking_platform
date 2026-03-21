"""
Tests for cross-platform path handling.

Tests the path normalization functions that convert Windows paths
to Unix-style paths for Docker containers.
"""
import pytest
from pathlib import Path
import sys
import os

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from example_complete_workflow import normalize_path_for_docker


class TestPathNormalization:
    """Test path normalization for Docker compatibility"""
    
    def test_normalize_windows_path(self):
        """Test Windows path conversion to Unix style"""
        windows_path = r"demo_output\downloaded\6LU7.pdb"
        result = normalize_path_for_docker(windows_path)
        assert "/" in result
        assert "\\" not in result
        assert result == "demo_output/downloaded/6LU7.pdb"
    
    def test_normalize_unix_path(self):
        """Test Unix path remains unchanged"""
        unix_path = "demo_output/downloaded/6LU7.pdb"
        result = normalize_path_for_docker(unix_path)
        assert result == "demo_output/downloaded/6LU7.pdb"
    
    def test_normalize_absolute_windows_path(self):
        """Test absolute Windows path conversion"""
        abs_path = r"C:\Users\test\demo_output\file.pdb"
        result = normalize_path_for_docker(abs_path)
        assert "/" in result
        assert "\\" not in result
        # On Windows, drive letter becomes C:
        assert "C:" in result or "c:" in result
    
    def test_normalize_relative_path(self):
        """Test relative path normalization"""
        rel_path = r"..\..\test\file.pdb"
        result = normalize_path_for_docker(rel_path)
        assert "/" in result
        assert "\\" not in result
        assert result == "../../test/file.pdb"
    
    def test_normalize_path_with_spaces(self):
        """Test path with spaces in directory names"""
        path_with_spaces = r"demo output\test file.pdb"
        result = normalize_path_for_docker(path_with_spaces)
        assert "/" in result
        assert "demo output" in result


class TestRelativePathGeneration:
    """Test relative path generation for PyMOL scripts"""
    
    def test_relative_path_calculation(self):
        """Test os.path.relpath for script generation"""
        script_dir = "demo_output/visualization"
        file_path = "demo_output/prepared_proteins/6LU7.pdbqt"
        
        rel_path = os.path.relpath(file_path, script_dir)
        # Should be ../prepared_proteins/6LU7.pdbqt
        assert ".." in rel_path
        assert "prepared_proteins" in rel_path
        assert "6LU7.pdbqt" in rel_path
    
    def test_relative_path_same_level(self):
        """Test relative path for files in same directory"""
        script_dir = "demo_output/visualization"
        file_path = "demo_output/visualization/test.pml"
        
        rel_path = os.path.relpath(file_path, script_dir)
        # Should be just test.pml
        assert rel_path == "test.pml" or rel_path == "./test.pml"


class TestPathIntegration:
    """Integration tests for path handling in workflow"""
    
    def test_path_types_accepted(self):
        """Test that function accepts different path types"""
        # String path
        str_path = "demo_output/test.pdb"
        result1 = normalize_path_for_docker(str_path)
        assert isinstance(result1, str)
        
        # Path object
        path_obj = Path("demo_output/test.pdb")
        result2 = normalize_path_for_docker(str(path_obj))
        assert isinstance(result2, str)
    
    def test_docker_volume_mount_paths(self):
        """Test paths used with Docker volume mounts"""
        # These paths should be relative to work with volume mounts
        paths = [
            "demo_output/downloaded/6LU7.pdb",
            "demo_output/prepared_proteins/6LU7.pdbqt",
            "demo_output/prepared_ligands/ATP.pdbqt",
            "demo_output/docking_results/moldina_out.pdbqt",
        ]
        
        for path in paths:
            result = normalize_path_for_docker(path)
            # Should not start with absolute path
            assert not result.startswith("/")
            # Should use forward slashes
            assert "/" in result or "." in result
            assert "\\" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
