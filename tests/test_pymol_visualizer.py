"""
Tests for PyMOL Visualizer Service
"""
import os
import pytest
import tempfile
import shutil
from pathlib import Path
from services.pymol_visualizer import PyMOLVisualizer


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def sample_receptor(temp_dir):
    """Create a sample receptor PDBQT file."""
    receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
    with open(receptor_path, 'w') as f:
        f.write("ATOM      1  N   ALA A   1      10.000  10.000  10.000  1.00  0.00    -0.416 N\n")
        f.write("ATOM      2  CA  ALA A   1      11.000  10.000  10.000  1.00  0.00     0.000 C\n")
        f.write("ATOM      3  C   ALA A   1      12.000  10.000  10.000  1.00  0.00     0.000 C\n")
        f.write("ATOM      4  O   ALA A   1      13.000  10.000  10.000  1.00  0.00     0.000 OA\n")
    return receptor_path


@pytest.fixture
def sample_ligands(temp_dir):
    """Create sample ligand PDBQT files."""
    ligand_paths = []
    for i in range(3):
        ligand_path = os.path.join(temp_dir, f"ligand_{i+1}.pdbqt")
        with open(ligand_path, 'w') as f:
            f.write(f"REMARK Pose {i+1}\n")
            f.write(f"ATOM      1  C   LIG A   1      {10+i}.000  10.000  10.000  1.00  0.00     0.000 C\n")
            f.write(f"ATOM      2  O   LIG A   1      {11+i}.000  10.000  10.000  1.00  0.00     0.000 OA\n")
        ligand_paths.append(ligand_path)
    return ligand_paths


class TestPyMOLVisualizerInit:
    """Test PyMOLVisualizer initialization."""
    
    def test_init_default(self):
        """Test initialization with default parameters."""
        visualizer = PyMOLVisualizer()
        assert visualizer.pymol_path is None or isinstance(visualizer.pymol_path, str)
        assert isinstance(visualizer.pymol_available, bool)
    
    def test_init_with_custom_path(self):
        """Test initialization with custom PyMOL path."""
        custom_path = "/custom/pymol/path"
        visualizer = PyMOLVisualizer(pymol_path=custom_path)
        # Check that custom path is stored
        assert visualizer.pymol_path == custom_path
        # Note: pymol_available depends on whether pymol is in PATH as fallback
        # On systems with pymol installed, it may still be True


class TestScriptGeneration:
    """Test PyMOL script generation."""
    
    def test_generate_basic_script(self, sample_receptor, sample_ligands, temp_dir):
        """Test basic script generation."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            image_output=None  # Don't render image in tests
        )
        
        # Check result dictionary
        assert result["script_path"] == output_script
        assert result["num_ligands"] == 3
        assert result["rendered"] is False
        assert os.path.exists(output_script)
        
        # Check script content
        with open(output_script, 'r') as f:
            content = f.read()
            assert "load" in content.lower()
            assert "receptor" in content.lower()
            assert "ligand_1" in content
            assert "ligand_2" in content
            assert "ligand_3" in content
    
    def test_generate_script_with_affinities(self, sample_receptor, sample_ligands, temp_dir):
        """Test script generation with affinity color coding."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_affinity.pml")
        affinities = [-8.5, -7.2, -6.1]
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            affinities=affinities,
            image_output=None
        )
        
        assert result["affinities"] == affinities
        assert os.path.exists(output_script)
        
        # Check affinity comments in script
        with open(output_script, 'r') as f:
            content = f.read()
            assert "-8.5" in content or "-8.50" in content
            assert "affinity_color" in content
            assert "set_color" in content
    
    def test_generate_script_with_surface(self, sample_receptor, sample_ligands, temp_dir):
        """Test script with surface display."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_surface.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            show_surface=True,
            image_output=None
        )
        
        assert result["settings"]["show_surface"] is True
        
        with open(output_script, 'r') as f:
            content = f.read()
            assert "show surface" in content.lower()
    
    def test_generate_script_no_surface(self, sample_receptor, sample_ligands, temp_dir):
        """Test script without surface display."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_no_surface.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            show_surface=False,
            image_output=None
        )
        
        assert result["settings"]["show_surface"] is False
    
    def test_generate_script_with_binding_site(self, sample_receptor, sample_ligands, temp_dir):
        """Test script with binding site highlighting."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_binding_site.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            show_binding_site=True,
            binding_site_radius=7.0,
            image_output=None
        )
        
        assert result["settings"]["show_binding_site"] is True
        assert result["settings"]["binding_site_radius"] == 7.0
        
        with open(output_script, 'r') as f:
            content = f.read()
            assert "binding_site" in content
            assert "within 7.0" in content or "within 7" in content
    
    def test_generate_script_single_ligand(self, sample_receptor, temp_dir):
        """Test script generation with single ligand."""
        visualizer = PyMOLVisualizer()
        ligand_path = os.path.join(temp_dir, "single_ligand.pdbqt")
        with open(ligand_path, 'w') as f:
            f.write("ATOM      1  C   LIG A   1      10.000  10.000  10.000  1.00  0.00     0.000 C\n")
        
        output_script = os.path.join(temp_dir, "test_single.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=[ligand_path],
            output_script=output_script,
            image_output=None
        )
        
        assert result["num_ligands"] == 1
        assert os.path.exists(output_script)
    
    def test_generate_script_creates_directory(self, sample_receptor, sample_ligands, temp_dir):
        """Test that script generation creates output directory if needed."""
        visualizer = PyMOLVisualizer()
        nested_dir = os.path.join(temp_dir, "nested", "directory")
        output_script = os.path.join(nested_dir, "test.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            image_output=None
        )
        
        assert os.path.exists(output_script)
        assert os.path.exists(nested_dir)


class TestVisualizeDockingResults:
    """Test visualize_docking_results method."""
    
    def test_visualize_docking_results_basic(self, sample_receptor, sample_ligands, temp_dir):
        """Test visualization of docking results."""
        visualizer = PyMOLVisualizer()
        output_dir = os.path.join(temp_dir, "visualization")
        
        docking_results = [
            {"output_file": sample_ligands[0], "best_affinity": -8.5},
            {"output_file": sample_ligands[1], "best_affinity": -7.2},
            {"output_file": sample_ligands[2], "best_affinity": -6.1}
        ]
        
        result = visualizer.visualize_docking_results(
            receptor_path=sample_receptor,
            docking_results=docking_results,
            output_dir=output_dir
        )
        
        assert result["total_results"] == 3
        assert result["visualized_results"] == 3
        assert os.path.exists(result["script_path"])
        assert len(result["affinities"]) == 3
        # Should be sorted by affinity (best first)
        assert result["affinities"][0] == -8.5
        assert result["affinities"][-1] == -6.1
    
    def test_visualize_docking_results_top_n(self, sample_receptor, sample_ligands, temp_dir):
        """Test visualization with top N limit."""
        visualizer = PyMOLVisualizer()
        output_dir = os.path.join(temp_dir, "visualization_topn")
        
        docking_results = [
            {"output_file": sample_ligands[0], "best_affinity": -8.5},
            {"output_file": sample_ligands[1], "best_affinity": -7.2},
            {"output_file": sample_ligands[2], "best_affinity": -6.1}
        ]
        
        result = visualizer.visualize_docking_results(
            receptor_path=sample_receptor,
            docking_results=docking_results,
            output_dir=output_dir,
            top_n=2
        )
        
        assert result["total_results"] == 3
        assert result["visualized_results"] == 2
        assert result["top_n"] == 2
        assert len(result["affinities"]) == 2
    
    def test_visualize_docking_results_no_affinities(self, sample_receptor, sample_ligands, temp_dir):
        """Test visualization without affinity data."""
        visualizer = PyMOLVisualizer()
        output_dir = os.path.join(temp_dir, "visualization_no_aff")
        
        docking_results = [
            {"output_file": sample_ligands[0]},
            {"output_file": sample_ligands[1]},
            {"output_file": sample_ligands[2]}
        ]
        
        result = visualizer.visualize_docking_results(
            receptor_path=sample_receptor,
            docking_results=docking_results,
            output_dir=output_dir
        )
        
        assert result["total_results"] == 3
        assert result["visualized_results"] == 3
        assert result["affinities"] is None
    
    def test_visualize_docking_results_missing_files(self, sample_receptor, temp_dir):
        """Test error handling when result files are missing."""
        visualizer = PyMOLVisualizer()
        output_dir = os.path.join(temp_dir, "visualization_missing")
        
        docking_results = [
            {"output_file": "/nonexistent/file.pdbqt"}
        ]
        
        with pytest.raises(ValueError, match="No valid docking result files found"):
            visualizer.visualize_docking_results(
                receptor_path=sample_receptor,
                docking_results=docking_results,
                output_dir=output_dir
            )


class TestPyMOLVisualizerEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_ligand_list(self, sample_receptor, temp_dir):
        """Test script generation with empty ligand list."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_empty.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=[],
            output_script=output_script,
            image_output=None
        )
        
        assert result["num_ligands"] == 0
        assert os.path.exists(output_script)
        
        # Script should still work (receptor only)
        with open(output_script, 'r') as f:
            content = f.read()
            assert "receptor" in content.lower()
    
    def test_mismatched_affinities_length(self, sample_receptor, sample_ligands, temp_dir):
        """Test handling of mismatched affinity list length."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_mismatch.pml")
        
        # Provide fewer affinities than ligands
        affinities = [-8.5, -7.2]  # Only 2, but 3 ligands
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            affinities=affinities,
            image_output=None
        )
        
        # Should still generate script (affinities will be applied to first N ligands)
        assert os.path.exists(output_script)
    
    def test_custom_image_settings(self, sample_receptor, sample_ligands, temp_dir):
        """Test custom image rendering settings."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test_custom.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands[:1],
            output_script=output_script,
            image_output=os.path.join(temp_dir, "custom.png"),
            image_width=2560,
            image_height=1440,
            image_dpi=600,
            ray_trace=False
        )
        
        # Check that settings are recorded (image won't render without PyMOL)
        assert os.path.exists(output_script)
        
        with open(output_script, 'r') as f:
            content = f.read()
            assert "2560" in content
            assert "1440" in content
            assert "dpi=600" in content


class TestSessionFile:
    """Test PyMOL session file generation."""
    
    def test_session_file_path(self, sample_receptor, sample_ligands, temp_dir):
        """Test that session file path is generated correctly."""
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, "test.pml")
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script,
            image_output=None
        )
        
        expected_session = os.path.join(temp_dir, "test.pse")
        assert result["session_path"] == expected_session
        
        # Check session save command in script
        with open(output_script, 'r') as f:
            content = f.read()
            assert "save" in content.lower()
            assert ".pse" in content


class TestRelativePaths:
    '''Test relative path generation for cross-platform compatibility.'''
    
    def test_paths_are_relative(self, sample_receptor, sample_ligands, temp_dir):
        '''Test that generated scripts use relative paths.'''
        visualizer = PyMOLVisualizer()
        output_script = os.path.join(temp_dir, 'visualization', 'test.pml')
        
        result = visualizer.generate_pymol_script(
            receptor_path=sample_receptor,
            ligand_paths=sample_ligands,
            output_script=output_script
        )
        
        # Read generated script
        assert os.path.exists(output_script)
        with open(output_script, 'r') as f:
            script_content = f.read()
        
        # Check that paths don't contain absolute Windows or Linux paths
        assert 'C:\\' not in script_content
        assert 'C:/' not in script_content
        assert '/app/' not in script_content  # Docker paths
        
        # Should contain relative paths with forward slashes
        assert 'load ../' in script_content or 'load ./' in script_content
