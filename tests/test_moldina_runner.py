"""
Tests for Moldina Runner

Tests the Moldina runner functionality including:
- WSL detection and configuration
- Path conversion (Windows <-> WSL)
- Config file generation
- Moldina execution (mocked)
- Results parsing
"""
import os
import platform
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from services.moldina_runner import MoldinaRunner


# Skip all tests if running on CI without WSL
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows" and os.environ.get("CI") == "true",
    reason="WSL not available in CI environment"
)


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests"""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for WSL commands"""
    with patch('services.moldina_runner.subprocess') as mock_sub:
        yield mock_sub


@pytest.fixture
def mock_platform_windows():
    """Mock platform to simulate Windows"""
    with patch('services.moldina_runner.platform.system', return_value='Windows'):
        yield


@pytest.fixture
def mock_platform_linux():
    """Mock platform to simulate Linux"""
    with patch('services.moldina_runner.platform.system', return_value='Linux'):
        yield


class TestMoldinaRunnerInit:
    """Test Moldina runner initialization"""
    
    def test_init_with_custom_cache(self, temp_dir, mock_platform_linux):
        """Test initialization with custom cache directory"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            runner = MoldinaRunner(cache_dir=temp_dir)
            assert runner.cache_dir == Path(temp_dir)
            assert runner.is_windows is False
    
    def test_init_default_cache(self, mock_platform_linux):
        """Test initialization with default cache directory"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            runner = MoldinaRunner()
            expected_cache = Path.home() / ".docking_cache" / "moldina"
            assert runner.cache_dir == expected_cache
    
    def test_init_windows_no_wsl(self, mock_platform_windows, mock_subprocess):
        """Test initialization fails on Windows without WSL"""
        mock_subprocess.run.return_value = Mock(returncode=1)
        
        with pytest.raises(RuntimeError, match="WSL not found"):
            MoldinaRunner()
    
    def test_init_windows_with_wsl(self, mock_platform_windows, mock_subprocess):
        """Test initialization succeeds on Windows with WSL"""
        # Mock WSL check
        mock_subprocess.run.side_effect = [
            Mock(returncode=0),  # WSL check
            Mock(returncode=0, stdout='exists\n')  # Moldina check
        ]
        
        runner = MoldinaRunner()
        assert runner.is_windows is True
        assert runner.wsl_distro == "Ubuntu"
    
    def test_init_no_moldina(self, mock_platform_linux, mock_subprocess):
        """Test initialization fails without Moldina"""
        with patch('services.moldina_runner.shutil.which', return_value=None):
            with pytest.raises(RuntimeError, match="Moldina not found"):
                MoldinaRunner()


class TestWSLFunctionality:
    """Test WSL-related functionality"""
    
    def test_check_wsl_success(self, mock_platform_windows, mock_subprocess):
        """Test WSL check succeeds"""
        # Set return value instead of side_effect for repeated calls
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='exists\n')
        
        runner = MoldinaRunner()
        # Reset to test _check_wsl independently
        mock_subprocess.run.return_value = Mock(returncode=0)
        assert runner._check_wsl() is True
    
    def test_check_wsl_failure(self, mock_platform_windows, mock_subprocess):
        """Test WSL check fails"""
        mock_subprocess.run.return_value = Mock(returncode=1)
        
        with pytest.raises(RuntimeError):
            MoldinaRunner()
    
    def test_windows_path_to_wsl(self, mock_platform_windows, mock_subprocess):
        """Test Windows to WSL path conversion"""
        mock_subprocess.run.side_effect = [
            Mock(returncode=0),  # WSL check
            Mock(returncode=0, stdout='exists\n')  # Moldina check
        ]
        
        runner = MoldinaRunner()
        
        # Test various Windows paths
        assert runner._windows_path_to_wsl(r"C:\Users\test\file.pdbqt") == "/mnt/c/Users/test/file.pdbqt"
        assert runner._windows_path_to_wsl(r"D:\Data\protein.pdb") == "/mnt/d/Data/protein.pdb"
    
    def test_wsl_path_to_windows(self, mock_platform_windows, mock_subprocess):
        """Test WSL to Windows path conversion"""
        mock_subprocess.run.side_effect = [
            Mock(returncode=0),  # WSL check
            Mock(returncode=0, stdout='exists\n')  # Moldina check
        ]
        
        runner = MoldinaRunner()
        
        # Test various WSL paths
        assert runner._wsl_path_to_windows("/mnt/c/Users/test/file.pdbqt") == "C:\\Users\\test\\file.pdbqt"
        assert runner._wsl_path_to_windows("/mnt/d/Data/protein.pdb") == "D:\\Data\\protein.pdb"
    
    def test_run_wsl_command(self, mock_platform_windows, mock_subprocess):
        """Test running WSL command"""
        mock_subprocess.run.side_effect = [
            Mock(returncode=0),  # WSL check
            Mock(returncode=0, stdout='exists\n'),  # Moldina check
            Mock(returncode=0, stdout='test output', stderr='')  # Actual command
        ]
        
        runner = MoldinaRunner()
        result = runner._run_wsl_command(['ls', '-l'])
        
        assert result.returncode == 0
        assert result.stdout == 'test output'


class TestMoldinaExecution:
    """Test Moldina docking execution"""
    
    @pytest.fixture
    def runner_linux(self, temp_dir, mock_platform_linux):
        """Create runner for Linux"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)
    
    @pytest.fixture
    def runner_windows(self, temp_dir, mock_platform_windows, mock_subprocess):
        """Create runner for Windows"""
        mock_subprocess.run.side_effect = [
            Mock(returncode=0),  # WSL check
            Mock(returncode=0, stdout='exists\n')  # Moldina check
        ]
        return MoldinaRunner(cache_dir=temp_dir)
    
    def test_run_moldina_config_generation(self, runner_linux, temp_dir, mock_subprocess):
        """Test Moldina config file generation"""
        # Create dummy input files
        receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
        ligand_path = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        
        Path(receptor_path).touch()
        Path(ligand_path).touch()
        
        # Mock subprocess for Moldina execution
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')
        
        # Mock _parse_moldina_results to avoid file checks
        with patch.object(runner_linux, '_parse_moldina_results', return_value=[]):
            runner_linux.run_moldina_docking(
                receptor_path=receptor_path,
                ligand_paths=[ligand_path],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                cpu=2,
                population=100,
                exhaustiveness=4
            )
        
        # Check center/box config file
        center_path = os.path.join(output_dir, "center.txt")
        assert os.path.exists(center_path)
        with open(center_path, 'r') as f:
            center_content = f.read()
        assert 'center_x = 0' in center_content
        assert 'size_x = 20' in center_content

        # Check parameters summary file
        params_path = os.path.join(output_dir, "parameters.txt")
        assert os.path.exists(params_path)
        with open(params_path, 'r') as f:
            params_content = f.read()
        assert 'receptor = ' in params_content
        assert 'search_alg_PSO' in params_content
    
    def test_run_moldina_ligands_list(self, runner_linux, temp_dir, mock_subprocess):
        """Ligands are docked one at a time: each gets its own subprocess.run call
        containing only that ligand's path (not batched into a single invocation,
        which previously caused OOM kills on larger batches)."""
        receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
        ligand1 = os.path.join(temp_dir, "ligand1.pdbqt")
        ligand2 = os.path.join(temp_dir, "ligand2.pdbqt")
        output_dir = os.path.join(temp_dir, "output")

        for path in [receptor_path, ligand1, ligand2]:
            Path(path).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        runner_linux.run_moldina_docking(
            receptor_path=receptor_path,
            ligand_paths=[ligand1, ligand2],
            output_dir=output_dir,
            center=(0, 0, 0),
            box_size=(20, 20, 20)
        )

        # One subprocess.run call per ligand
        assert mock_subprocess.run.call_count == 2
        call1_args = mock_subprocess.run.call_args_list[0][0][0]
        call2_args = mock_subprocess.run.call_args_list[1][0][0]
        assert ligand1 in call1_args and ligand2 not in call1_args
        assert ligand2 in call2_args and ligand1 not in call2_args

    def test_run_moldina_command_construction(self, runner_linux, temp_dir, mock_subprocess):
        """Test Moldina command is correctly constructed"""
        receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
        ligand_path = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")

        for path in [receptor_path, ligand_path]:
            Path(path).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        runner_linux.run_moldina_docking(
            receptor_path=receptor_path,
            ligand_paths=[ligand_path],
            output_dir=output_dir,
            center=(10.5, 20.3, -5.2),
            box_size=(25, 30, 35),
            cpu=4,
            population=200
        )

        # Check that subprocess.run was called
        assert mock_subprocess.run.called
        call_args = mock_subprocess.run.call_args[0][0]

        # Verify command structure
        assert runner_linux.moldina_path in call_args
        assert '--config' in call_args
        assert '--cpu' in call_args
        assert '4' in call_args
        assert '--population' in call_args
        assert '200' in call_args


class TestAffinitySort:
    """Tests for affinity sort key (M3 fix: None-safe comparison)."""

    def test_affinity_sort_puts_none_last(self):
        results = [
            {"best_affinity": None},
            {"best_affinity": -7.5},
            {"best_affinity": -8.0},
        ]
        results.sort(key=lambda x: x['best_affinity'] if x['best_affinity'] is not None else float('inf'))
        assert results[0]["best_affinity"] == -8.0
        assert results[1]["best_affinity"] == -7.5
        assert results[2]["best_affinity"] is None

    def test_affinity_sort_zero_sorts_after_negative_before_none(self):
        results = [
            {"best_affinity": None},
            {"best_affinity": 0.0},
            {"best_affinity": -7.5},
        ]
        results.sort(key=lambda x: x['best_affinity'] if x['best_affinity'] is not None else float('inf'))
        assert results[0]["best_affinity"] == -7.5
        assert results[1]["best_affinity"] == 0.0
        assert results[2]["best_affinity"] is None


class TestResultsParsing:
    """Test parsing of Moldina results"""

    @pytest.fixture
    def runner(self, temp_dir, mock_platform_linux):
        """Create runner"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)
    
    def test_parse_vina_log_with_results(self, runner, temp_dir):
        """Test parsing log file with docking results"""
        log_content = """
Scoring function : vina
Rigid receptor: receptor.pdbqt
Grid center: X 10.000 Y 20.000 Z 30.000
Grid size  : X 20 Y 20 Z 20

mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -8.5      0.000      0.000
   2       -8.2      1.234      3.456
   3       -7.9      2.345      4.567
"""
        log_path = os.path.join(temp_dir, "test.log")
        with open(log_path, 'w') as f:
            f.write(log_content)
        
        results = runner._parse_vina_log(log_path)
        
        assert results["best_affinity"] == -8.5
        assert results["num_modes_found"] == 3
        assert len(results["modes"]) == 3
        assert results["modes"][0]["mode"] == 1
        assert results["modes"][0]["affinity"] == -8.5
        assert results["modes"][1]["rmsd_lb"] == 1.234
    
    def test_parse_vina_log_no_results(self, runner, temp_dir):
        """Test parsing log file with no results"""
        log_content = "Moldina started...\nError occurred\n"
        log_path = os.path.join(temp_dir, "test.log")
        with open(log_path, 'w') as f:
            f.write(log_content)
        
        results = runner._parse_vina_log(log_path)
        
        assert results["best_affinity"] is None
        assert results["num_modes_found"] == 0
        assert len(results["modes"]) == 0
    
    def test_parse_vina_log_missing_file(self, runner):
        """Test parsing non-existent log file"""
        results = runner._parse_vina_log("/nonexistent/file.log")
        
        assert results["best_affinity"] is None
        assert results["num_modes_found"] == 0
    
    def test_parse_moldina_results(self, runner, temp_dir):
        """Test parsing Moldina output files"""
        ligand1 = os.path.join(temp_dir, "input", "ligand1.pdbqt")
        ligand2 = os.path.join(temp_dir, "input", "ligand2.pdbqt")
        
        os.makedirs(os.path.join(temp_dir, "input"), exist_ok=True)
        Path(ligand1).touch()
        Path(ligand2).touch()
        
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # Create output files
        output1 = os.path.join(output_dir, "ligand1_out.pdbqt")
        output2 = os.path.join(output_dir, "ligand2_out.pdbqt")
        Path(output1).write_text("REMARK VINA RESULT:    -8.5      0.000      0.000\n")
        Path(output2).write_text("REMARK VINA RESULT:    -7.2      0.000      0.000\n")
        
        # Create log files
        log1 = os.path.join(output_dir, "ligand1_out.log")
        log2 = os.path.join(output_dir, "ligand2_out.log")
        
        log_template = """mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       {affinity}      0.000      0.000
"""
        Path(log1).write_text(log_template.format(affinity="-8.5"))
        Path(log2).write_text(log_template.format(affinity="-7.2"))
        
        results = runner._parse_moldina_results(output_dir, [ligand1, ligand2])
        
        assert len(results) == 2
        # Should be sorted by affinity (most negative first)
        assert results[0]["ligand_id"] == "ligand1"
        assert results[0]["best_affinity"] == -8.5
        assert results[1]["ligand_id"] == "ligand2"
        assert results[1]["best_affinity"] == -7.2


class TestMoldinaRunnerIntegration:
    """Integration tests for full docking workflow"""
    
    @pytest.fixture
    def runner(self, temp_dir, mock_platform_linux):
        """Create runner"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)
    
    def test_full_docking_workflow(self, runner, temp_dir, mock_subprocess):
        """Test complete docking workflow"""
        # Setup
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        
        Path(receptor).touch()
        Path(ligand).touch()
        
        # Mock Moldina execution
        def mock_moldina_run(*args, **kwargs):
            # Create output files
            os.makedirs(output_dir, exist_ok=True)
            out_file = os.path.join(output_dir, "ligand_out.pdbqt")
            log_file = os.path.join(output_dir, "ligand_out.log")
            
            Path(out_file).write_text("MODEL 1\nREMARK VINA RESULT: -8.5\nENDMDL\n")
            Path(log_file).write_text("""mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -8.5      0.000      0.000
""")
            return Mock(returncode=0, stdout='Moldina completed', stderr='')
        
        mock_subprocess.run.side_effect = mock_moldina_run
        
        # Run docking
        results = runner.run_moldina_docking(
            receptor_path=receptor,
            ligand_paths=[ligand],
            output_dir=output_dir,
            center=(0, 0, 0),
            box_size=(20, 20, 20)
        )
        
        # Verify results
        assert results["status"] == "ok"
        assert results["total"] == 1
        assert results["successful"] == 1
        assert len(results["results"]) == 1
        assert results["results"][0]["best_affinity"] == -8.5

    def test_one_bad_ligand_does_not_kill_whole_batch(self, runner, temp_dir, mock_subprocess):
        """A single failing ligand (e.g. OOM-killed, exit -9) is recorded as an error
        but does not abort docking for the other ligands in the batch."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        good_ligand = os.path.join(temp_dir, "good.pdbqt")
        bad_ligand = os.path.join(temp_dir, "bad.pdbqt")
        output_dir = os.path.join(temp_dir, "output")

        Path(receptor).touch()
        Path(good_ligand).touch()
        Path(bad_ligand).touch()

        def mock_moldina_run(cmd, *args, **kwargs):
            os.makedirs(output_dir, exist_ok=True)
            if "bad" in cmd[cmd.index('--ligand') + 1]:
                return Mock(returncode=-9, stdout='', stderr='')
            out_file = os.path.join(output_dir, "good_out.pdbqt")
            log_file = os.path.join(output_dir, "good_out.log")
            Path(out_file).write_text("MODEL 1\nREMARK VINA RESULT: -8.5\nENDMDL\n")
            Path(log_file).write_text("""mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -8.5      0.000      0.000
""")
            return Mock(returncode=0, stdout='Moldina completed', stderr='')

        mock_subprocess.run.side_effect = mock_moldina_run

        results = runner.run_moldina_docking(
            receptor_path=receptor,
            ligand_paths=[good_ligand, bad_ligand],
            output_dir=output_dir,
            center=(0, 0, 0),
            box_size=(20, 20, 20)
        )

        assert results["status"] == "ok"
        assert results["total"] == 2
        assert results["successful"] == 1
        assert results["failed"] == 1
        assert len(results["results"]) == 1
        assert results["results"][0]["ligand_id"] == "good"
        assert len(results["errors"]) == 1
        assert "bad" in results["errors"][0]

    def test_all_ligands_failing_raises_runtime_error(self, runner, temp_dir, mock_subprocess):
        """If every ligand fails, the whole docking call still raises (no false 'ok' status)."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")

        Path(receptor).touch()
        Path(ligand).touch()

        mock_subprocess.run.return_value = Mock(returncode=-9, stdout='', stderr='')

        with pytest.raises(RuntimeError, match="failed for all ligands"):
            runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=[ligand],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20)
            )
