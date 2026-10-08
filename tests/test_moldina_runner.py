"""
Tests for Moldina Runner

Tests the Moldina runner functionality including:
- WSL detection and configuration
- Path conversion (Windows <-> WSL)
- Config file generation
- Moldina execution (mocked)
- Results parsing
"""
import json
import os
import platform
import pytest
import tempfile
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open

from services.moldina_runner import (
    MoldinaRunner,
    _build_docking_command,
    _clamp_docking_params,
    _detect_effective_cpu_count,
)


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


class _FinishedProc:
    """Popen stand-in for an already-finished process, built from a
    subprocess.run-style result (returncode/stdout/stderr)."""

    def __init__(self, run_result):
        self.returncode = run_result.returncode
        self._stdout = run_result.stdout
        self._stderr = run_result.stderr

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def terminate(self):
        pass

    def kill(self):
        pass


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for WSL commands.

    Docking runs Moldina through subprocess.Popen (so a stop request can
    kill it, see _run_cancellable); Popen calls are routed through
    ``mock_sub.run`` so tests keep configuring and asserting on ``.run``
    exactly as for the WSL/availability checks that still use it.
    """
    with patch('services.moldina_runner.subprocess') as mock_sub:
        mock_sub.Popen.side_effect = lambda cmd, **kwargs: _FinishedProc(mock_sub.run(cmd))
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
    
    def test_run_moldina_docking_clamps_out_of_range_params(self, runner_linux, temp_dir, mock_subprocess):
        """run_docking's numeric parameters are clamped to sane bounds before
        being passed to the Moldina CLI -- e.g. a caller-supplied
        population=0 or exhaustiveness=999 must not reach the subprocess
        call unmodified."""
        receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
        ligand_path = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        Path(receptor_path).touch()
        Path(ligand_path).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        with patch(
            'services.moldina_runner._detect_effective_cpu_count', return_value=4,
        ):
            runner_linux.run_moldina_docking(
                receptor_path=receptor_path,
                ligand_paths=[ligand_path],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                cpu=999,           # clamp to detected CPU count (4)
                population=0,      # clamp up to minimum (5)
                exhaustiveness=999,  # clamp down to maximum (64)
                num_modes=0,       # clamp up to minimum (1)
                energy_range=-5.0,  # clamp up to minimum (0.1)
            )

        moldina_cmd = mock_subprocess.run.call_args[0][0]
        assert moldina_cmd[moldina_cmd.index('--cpu') + 1] == '4'
        assert moldina_cmd[moldina_cmd.index('--population') + 1] == '5'
        assert moldina_cmd[moldina_cmd.index('--exhaustiveness') + 1] == '64'
        assert moldina_cmd[moldina_cmd.index('--num_modes') + 1] == '1'
        assert moldina_cmd[moldina_cmd.index('--energy_range') + 1] == '0.1'

        params_content = Path(output_dir, "parameters.txt").read_text()
        assert "population = 5" in params_content
        assert "exhaustiveness = 64" in params_content

    def test_run_moldina_docking_leaves_in_range_params_untouched(self, runner_linux, temp_dir, mock_subprocess):
        receptor_path = os.path.join(temp_dir, "receptor.pdbqt")
        ligand_path = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        Path(receptor_path).touch()
        Path(ligand_path).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=8):
            runner_linux.run_moldina_docking(
                receptor_path=receptor_path,
                ligand_paths=[ligand_path],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                cpu=2,
                population=500,
                exhaustiveness=8,
                num_modes=15,
                energy_range=100.0,
            )

        moldina_cmd = mock_subprocess.run.call_args[0][0]
        assert moldina_cmd[moldina_cmd.index('--cpu') + 1] == '2'
        assert moldina_cmd[moldina_cmd.index('--population') + 1] == '500'
        assert moldina_cmd[moldina_cmd.index('--exhaustiveness') + 1] == '8'
        assert moldina_cmd[moldina_cmd.index('--num_modes') + 1] == '15'
        assert moldina_cmd[moldina_cmd.index('--energy_range') + 1] == '100.0'

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

        # cpu is clamped to the machine's CPUs; pin them so a 2-CPU CI
        # runner doesn't turn the requested 4 into 2.
        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=8):
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


class TestClampDockingParams:
    """Unit tests for the bounds-clamping helper itself, independent of the
    subprocess plumbing exercised in TestMoldinaExecution."""

    def test_in_range_values_pass_through_unchanged(self):
        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=8):
            result = _clamp_docking_params(
                cpu=2, population=500, exhaustiveness=8, num_modes=15, energy_range=100.0,
            )
        assert result == (2, 500, 8, 15, 100.0)

    def test_out_of_range_values_are_clamped(self):
        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=4):
            result = _clamp_docking_params(
                cpu=999, population=0, exhaustiveness=999, num_modes=0, energy_range=-5.0,
            )
        assert result == (4, 5, 64, 1, 0.1)

    def test_cpu_clamped_to_zero_or_negative_becomes_one(self):
        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=4):
            result = _clamp_docking_params(
                cpu=0, population=500, exhaustiveness=8, num_modes=15, energy_range=100.0,
            )
        assert result[0] == 1


class TestDetectEffectiveCpuCount:
    """Tests for the cgroup-aware CPU budget detection used to clamp
    max_workers. os.cpu_count() does not reflect a Docker Compose
    `deploy.resources.limits.cpus` quota (confirmed in practice: a
    container limited to cpus: '4' still reports the host's full core
    count via os.cpu_count()), so this reads the cgroup files directly."""

    @staticmethod
    def _opener_for(paths: dict):
        """paths: {file_path: content}. Any other path raises FileNotFoundError,
        matching what happens when a given cgroup v1/v2 file doesn't exist."""
        def _open(path, *args, **kwargs):
            if path not in paths:
                raise FileNotFoundError(path)
            return mock_open(read_data=paths[path])(*args, **kwargs)
        return _open

    def test_cgroup_v2_quota_present(self):
        """cgroup v2 cpu.max with a real quota lower than os.cpu_count() returns
        the effective (quota-limited) core count."""
        opener = self._opener_for({"/sys/fs/cgroup/cpu.max": "400000 100000\n"})
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=20):
            assert _detect_effective_cpu_count() == 4

    def test_cgroup_v2_fractional_quota_rounds_down(self):
        """A quota that doesn't divide evenly rounds down — never claim more
        schedulable CPU than is actually guaranteed."""
        opener = self._opener_for({"/sys/fs/cgroup/cpu.max": "350000 100000\n"})
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=20):
            assert _detect_effective_cpu_count() == 3

    def test_cgroup_v2_unlimited_falls_back_to_os_cpu_count(self):
        """cgroup v2 'max' quota means no limit enforced — fall back to os.cpu_count()."""
        opener = self._opener_for({"/sys/fs/cgroup/cpu.max": "max 100000\n"})
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=16):
            assert _detect_effective_cpu_count() == 16

    def test_cgroup_v1_quota_present(self):
        """Falls back to cgroup v1 quota/period files when v2's cpu.max is absent."""
        opener = self._opener_for({
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "200000\n",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n",
        })
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=20):
            assert _detect_effective_cpu_count() == 2

    def test_cgroup_v1_unlimited_falls_back_to_os_cpu_count(self):
        """cgroup v1 quota of -1 means unlimited — fall back to os.cpu_count()."""
        opener = self._opener_for({
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1\n",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n",
        })
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=8):
            assert _detect_effective_cpu_count() == 8

    def test_no_cgroup_files_falls_back_to_os_cpu_count(self):
        """Neither cgroup v1 nor v2 files exist (e.g. not running in a container) —
        fall back to os.cpu_count()."""
        with patch('builtins.open', side_effect=FileNotFoundError), \
                patch('services.moldina_runner.os.cpu_count', return_value=12):
            assert _detect_effective_cpu_count() == 12

    def test_quota_higher_than_real_hardware_clamps_to_os_cpu_count(self):
        """If the configured quota assumes more cores than this machine actually
        has (e.g. `cpus: '10'` in docker-compose.yml, deployed on a 4-core box),
        the real hardware limit wins — the quota alone is never trusted blindly."""
        opener = self._opener_for({"/sys/fs/cgroup/cpu.max": "1000000 100000\n"})  # quota = 10
        with patch('builtins.open', side_effect=opener), \
                patch('services.moldina_runner.os.cpu_count', return_value=4):
            assert _detect_effective_cpu_count() == 4


class TestMoldinaRunnerParallel:
    """Tests for optional concurrent (parallel=True) ligand docking."""

    @pytest.fixture
    def runner(self, temp_dir, mock_platform_linux):
        """Create runner"""
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)

    @staticmethod
    def _write_success_outputs(output_dir, ligand_id, affinity=-8.5):
        out_file = os.path.join(output_dir, f"{ligand_id}_out.pdbqt")
        log_file = os.path.join(output_dir, f"{ligand_id}_out.log")
        Path(out_file).write_text("MODEL 1\nREMARK VINA RESULT: -8.5\nENDMDL\n")
        Path(log_file).write_text(
            "mode |   affinity | dist from best mode\n"
            "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
            "-----+------------+----------+----------\n"
            f"   1       {affinity}      0.000      0.000\n"
        )

    def test_parallel_false_stays_sequential(self, runner, temp_dir, mock_subprocess):
        """parallel defaults to False — behavior is unchanged from before this feature."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand1 = os.path.join(temp_dir, "ligand1.pdbqt")
        ligand2 = os.path.join(temp_dir, "ligand2.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        for path in [receptor, ligand1, ligand2]:
            Path(path).touch()

        with patch('services.moldina_runner.ThreadPoolExecutor') as mock_pool:
            mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')
            runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=[ligand1, ligand2],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
            )

        mock_pool.assert_not_called()
        assert mock_subprocess.run.call_count == 2

    def test_parallel_all_ligands_docked(self, runner, temp_dir, mock_subprocess):
        """With parallel=True, every ligand still gets docked and results are complete."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand_paths = []
        for i in range(3):
            p = os.path.join(temp_dir, f"ligand{i}.pdbqt")
            Path(p).touch()
            ligand_paths.append(p)
        Path(receptor).touch()
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        def mock_moldina_run(cmd, *args, **kwargs):
            ligand_arg = cmd[cmd.index('--ligand') + 1]
            ligand_id = Path(ligand_arg).stem
            self._write_success_outputs(output_dir, ligand_id)
            return Mock(returncode=0, stdout='Moldina completed', stderr='')

        mock_subprocess.run.side_effect = mock_moldina_run

        with patch('services.moldina_runner.os.cpu_count', return_value=4):
            results = runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=ligand_paths,
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                parallel=True,
                max_workers=3,
            )

        assert results["status"] == "ok"
        assert results["total"] == 3
        assert results["successful"] == 3
        assert results["failed"] == 0
        assert {r["ligand_id"] for r in results["results"]} == {"ligand0", "ligand1", "ligand2"}
        # Every ligand's own PDBQT/log survived — none overwrote another's.
        for i in range(3):
            assert os.path.exists(os.path.join(output_dir, f"ligand{i}_out.pdbqt"))

    def test_parallel_one_bad_ligand_does_not_kill_batch(self, runner, temp_dir, mock_subprocess):
        """Same resilience guarantee as sequential mode: one failing ligand doesn't
        abort the rest of a concurrent batch."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        good_ligand = os.path.join(temp_dir, "good.pdbqt")
        bad_ligand = os.path.join(temp_dir, "bad.pdbqt")
        output_dir = os.path.join(temp_dir, "output")

        Path(receptor).touch()
        Path(good_ligand).touch()
        Path(bad_ligand).touch()
        os.makedirs(output_dir, exist_ok=True)

        def mock_moldina_run(cmd, *args, **kwargs):
            ligand_arg = cmd[cmd.index('--ligand') + 1]
            if "bad" in ligand_arg:
                return Mock(returncode=-9, stdout='', stderr='')
            self._write_success_outputs(output_dir, "good")
            return Mock(returncode=0, stdout='Moldina completed', stderr='')

        mock_subprocess.run.side_effect = mock_moldina_run

        with patch('services.moldina_runner.os.cpu_count', return_value=4):
            results = runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=[good_ligand, bad_ligand],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                parallel=True,
                max_workers=2,
            )

        assert results["status"] == "ok"
        assert results["successful"] == 1
        assert results["failed"] == 1
        assert results["results"][0]["ligand_id"] == "good"
        assert any("bad" in err for err in results["errors"])

    def test_parallel_workers_clamped_to_ligand_count(self, runner, temp_dir, mock_subprocess):
        """Requesting more workers than ligands doesn't over-provision the thread pool."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand1 = os.path.join(temp_dir, "ligand1.pdbqt")
        ligand2 = os.path.join(temp_dir, "ligand2.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        for path in [receptor, ligand1, ligand2]:
            Path(path).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        with patch('services.moldina_runner.ThreadPoolExecutor', wraps=ThreadPoolExecutor) as mock_pool, \
                patch('services.moldina_runner.os.cpu_count', return_value=8):
            runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=[ligand1, ligand2],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                parallel=True,
                max_workers=10,
            )

        mock_pool.assert_called_once_with(max_workers=2)

    def test_parallel_workers_clamped_to_cpu_count(self, runner, temp_dir, mock_subprocess):
        """Requesting more workers than the container's real CPU budget clamps to
        whatever _detect_effective_cpu_count() reports, regardless of what the
        caller asked for. Patches the detector directly rather than os.cpu_count()
        so this stays correct even when run inside a real cgroup-limited container
        (where cgroup files would otherwise take precedence over an os.cpu_count()
        mock — see TestDetectEffectiveCpuCount for that logic in isolation)."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        ligand_paths = []
        for i in range(5):
            p = os.path.join(temp_dir, f"ligand{i}.pdbqt")
            Path(p).touch()
            ligand_paths.append(p)
        Path(receptor).touch()

        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        with patch('services.moldina_runner.ThreadPoolExecutor', wraps=ThreadPoolExecutor) as mock_pool, \
                patch('services.moldina_runner._detect_effective_cpu_count', return_value=2):
            runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=ligand_paths,
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                parallel=True,
                max_workers=10,
            )

        mock_pool.assert_called_once_with(max_workers=2)

    def test_parallel_ligands_genuinely_overlap(self, runner, temp_dir, mock_subprocess):
        """Two ligands are actually in flight at the same time under parallel=True —
        proven by reading docking_progress.json while both invocations are still
        blocked mid-flight, rather than relying on timing/sleep."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand1 = os.path.join(temp_dir, "ligand1.pdbqt")
        ligand2 = os.path.join(temp_dir, "ligand2.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        for path in [receptor, ligand1, ligand2]:
            Path(path).touch()
        os.makedirs(output_dir, exist_ok=True)

        both_started = threading.Event()
        seen_lock = threading.Lock()
        seen = set()
        progress_path = os.path.join(output_dir, "docking_progress.json")
        in_progress_snapshot = []

        def mock_moldina_run(cmd, *args, **kwargs):
            ligand_arg = cmd[cmd.index('--ligand') + 1]
            with seen_lock:
                seen.add(ligand_arg)
                if len(seen) == 2:
                    with open(progress_path) as f:
                        payload = json.load(f)
                    in_progress_snapshot.append(len(payload["in_progress"]))
                    both_started.set()
            # Neither call proceeds to "finish" until both have started, so
            # completion can never race ahead of the overlap check above.
            both_started.wait(timeout=5)

            ligand_id = Path(ligand_arg).stem
            self._write_success_outputs(output_dir, ligand_id)
            return Mock(returncode=0, stdout='Moldina completed', stderr='')

        mock_subprocess.run.side_effect = mock_moldina_run

        with patch('services.moldina_runner.os.cpu_count', return_value=4):
            results = runner.run_moldina_docking(
                receptor_path=receptor,
                ligand_paths=[ligand1, ligand2],
                output_dir=output_dir,
                center=(0, 0, 0),
                box_size=(20, 20, 20),
                parallel=True,
                max_workers=2,
            )

        assert both_started.is_set(), "both ligands should have started concurrently"
        assert in_progress_snapshot == [2]
        assert results["successful"] == 2

    def test_docking_progress_schema_has_in_progress_list(self, runner, temp_dir, mock_subprocess):
        """docking_progress.json uses the list-based in_progress schema for both
        sequential and parallel runs, ending in a 'done' status with an empty list."""
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        ligand = os.path.join(temp_dir, "ligand.pdbqt")
        output_dir = os.path.join(temp_dir, "output")
        Path(receptor).touch()
        Path(ligand).touch()
        os.makedirs(output_dir, exist_ok=True)

        def mock_moldina_run(cmd, *args, **kwargs):
            self._write_success_outputs(output_dir, "ligand")
            return Mock(returncode=0, stdout='Moldina completed', stderr='')

        mock_subprocess.run.side_effect = mock_moldina_run

        runner.run_moldina_docking(
            receptor_path=receptor,
            ligand_paths=[ligand],
            output_dir=output_dir,
            center=(0, 0, 0),
            box_size=(20, 20, 20),
        )

        with open(os.path.join(output_dir, "docking_progress.json")) as f:
            payload = json.load(f)

        assert payload["status"] == "done"
        assert payload["total"] == 1
        assert payload["completed"] == 1
        assert payload["in_progress"] == []


# ---------------------------------------------------------------------------
# Stop on request (Phase 13, Stage 13.1b) -- a STOP_REQUESTED flag file in
# output_dir halts the batch: no further ligands start, running Moldina
# processes are killed. These use real subprocess.TimeoutExpired (only Popen
# is replaced), so _run_cancellable's poll/terminate path really runs.
# ---------------------------------------------------------------------------

import subprocess as real_subprocess

from common.docking_stop import STOP_FLAG_NAME, request_docking_stop
from services.moldina_runner import _run_cancellable


class _DoneProc:
    """Moldina process that has already finished successfully."""

    returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def terminate(self):
        pass

    def kill(self):
        pass


class _HangingProc:
    """Moldina process that never finishes on its own; optionally requests
    a stop (writes the flag) the first time it's polled, i.e. mid-docking."""

    def __init__(self, stop_dir=None):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._stop_dir = stop_dir

    def communicate(self, timeout=None):
        if self.terminated or self.killed:
            self.returncode = -15
            return "", ""
        if self._stop_dir is not None:
            request_docking_stop(self._stop_dir)
        raise real_subprocess.TimeoutExpired("moldina", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class TestDockingStop:

    @pytest.fixture
    def runner(self, temp_dir, mock_platform_linux):
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)

    @staticmethod
    def _inputs(temp_dir, n):
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        Path(receptor).touch()
        ligands = []
        for i in range(1, n + 1):
            path = os.path.join(temp_dir, f"ligand{i}.pdbqt")
            Path(path).touch()
            ligands.append(path)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        return receptor, ligands, output_dir

    @staticmethod
    def _dock(runner, receptor, ligands, output_dir, **kwargs):
        return runner.run_moldina_docking(
            receptor_path=receptor,
            ligand_paths=ligands,
            output_dir=output_dir,
            center=(0, 0, 0),
            box_size=(20, 20, 20),
            **kwargs,
        )

    @staticmethod
    def _progress(output_dir):
        with open(os.path.join(output_dir, "docking_progress.json")) as f:
            return json.load(f)

    def test_stop_before_start_docks_nothing_and_does_not_raise(self, runner, temp_dir):
        receptor, ligands, output_dir = self._inputs(temp_dir, 3)
        request_docking_stop(output_dir)

        with patch('services.moldina_runner.subprocess.Popen') as popen:
            result = self._dock(runner, receptor, ligands, output_dir)

        popen.assert_not_called()
        assert result["status"] == "stopped"
        assert result["stopped"] is True
        assert result["successful"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 3
        assert result["errors"] == []
        assert self._progress(output_dir)["status"] == "stopped"

    def test_stop_between_ligands_skips_the_rest(self, runner, temp_dir):
        receptor, ligands, output_dir = self._inputs(temp_dir, 3)

        def first_ligand_then_stop(cmd, **kwargs):
            # The user clicks Stop while ligand 1 is finishing.
            request_docking_stop(output_dir)
            return _DoneProc()

        with patch('services.moldina_runner.subprocess.Popen', side_effect=first_ligand_then_stop) as popen:
            result = self._dock(runner, receptor, ligands, output_dir)

        assert popen.call_count == 1
        assert result["stopped"] is True
        assert result["successful"] == 1
        assert result["skipped"] == 2
        assert [r["ligand_id"] for r in result["results"]] == ["ligand1"]

    def test_stop_mid_ligand_kills_the_running_process(self, runner, temp_dir):
        receptor, ligands, output_dir = self._inputs(temp_dir, 2)
        proc = _HangingProc(stop_dir=output_dir)

        with patch('services.moldina_runner.subprocess.Popen', return_value=proc) as popen:
            result = self._dock(runner, receptor, ligands, output_dir)

        assert proc.terminated is True
        assert popen.call_count == 1  # ligand 2 never started
        assert result["stopped"] is True
        assert result["successful"] == 0
        assert result["failed"] == 0  # a killed ligand is not an error
        assert result["skipped"] == 2
        progress = self._progress(output_dir)
        assert progress["status"] == "stopped"
        assert progress["completed"] == 0
        assert progress["in_progress"] == []

    def test_stop_mid_run_in_parallel_mode(self, runner, temp_dir):
        receptor, ligands, output_dir = self._inputs(temp_dir, 6)
        procs = []

        def hanging(cmd, **kwargs):
            proc = _HangingProc(stop_dir=output_dir)
            procs.append(proc)
            return proc

        with patch('services.moldina_runner._detect_effective_cpu_count', return_value=4), \
                patch('services.moldina_runner.subprocess.Popen', side_effect=hanging):
            result = self._dock(runner, receptor, ligands, output_dir, parallel=True, max_workers=2)

        assert 1 <= len(procs) <= 2  # only ligands already in flight were started
        assert all(p.terminated for p in procs)
        assert result["stopped"] is True
        assert result["successful"] == 0
        assert result["skipped"] == 6

    def test_no_stop_flag_keeps_normal_status(self, runner, temp_dir):
        receptor, ligands, output_dir = self._inputs(temp_dir, 2)

        with patch('services.moldina_runner.subprocess.Popen', return_value=_DoneProc()):
            result = self._dock(runner, receptor, ligands, output_dir)

        assert result["status"] == "ok"
        assert result["stopped"] is False
        assert result["skipped"] == 0
        assert result["successful"] == 2
        assert not os.path.exists(os.path.join(output_dir, STOP_FLAG_NAME))


class TestRunCancellable:

    def test_timeout_kills_and_raises(self):
        proc = _HangingProc()
        with patch('services.moldina_runner.subprocess.Popen', return_value=proc):
            with pytest.raises(real_subprocess.TimeoutExpired):
                _run_cancellable(["moldina"], stop_check=lambda: False, timeout=0)
        assert proc.killed is True

    def test_stop_escalates_to_kill_when_terminate_is_ignored(self):
        class _StubbornProc(_HangingProc):
            def terminate(self):
                self.terminated_requested = True  # ignored -- keeps running

            def communicate(self, timeout=None):
                if self.killed:
                    return "", ""
                raise real_subprocess.TimeoutExpired("moldina", timeout)

        proc = _StubbornProc()
        with patch('services.moldina_runner.subprocess.Popen', return_value=proc), \
                patch('services.moldina_runner._STOP_KILL_GRACE', 0):
            assert _run_cancellable(["moldina"], stop_check=lambda: True) is None
        assert proc.terminated_requested is True
        assert proc.killed is True


class TestDockingAlgorithm:
    """Stage 15.1: docking_algorithm selects PSO ("moldina") or Monte Carlo ("vina")."""

    _SHARED = dict(cpu=2, population=300, exhaustiveness=8, num_modes=9, energy_range=3.0)

    @pytest.fixture
    def runner(self, temp_dir, mock_platform_linux):
        with patch.object(MoldinaRunner, '_find_moldina_executable', return_value='/usr/bin/Moldina'):
            return MoldinaRunner(cache_dir=temp_dir)

    @staticmethod
    def _files(temp_dir, n_ligands=1):
        receptor = os.path.join(temp_dir, "receptor.pdbqt")
        Path(receptor).touch()
        ligands = []
        for i in range(n_ligands):
            p = os.path.join(temp_dir, f"ligand{i}.pdbqt")
            Path(p).touch()
            ligands.append(p)
        return receptor, ligands, os.path.join(temp_dir, "output")

    def test_pso_command_has_population_and_pso_flag(self):
        cmd = _build_docking_command("m", "r", "l", "c", "o", algorithm="moldina", **self._SHARED)
        assert cmd[-3:] == ['--population', '300', '--search_alg_PSO']

    def test_vina_command_omits_pso_flags(self):
        cmd = _build_docking_command("m", "r", "l", "c", "o", algorithm="vina", **self._SHARED)
        assert '--search_alg_PSO' not in cmd
        assert '--population' not in cmd

    def test_shared_flags_identical_in_both_modes(self):
        pso = _build_docking_command("m", "r", "l", "c", "o", algorithm="moldina", **self._SHARED)
        vina = _build_docking_command("m", "r", "l", "c", "o", algorithm="vina", **self._SHARED)
        assert pso[:len(vina)] == vina
        for flag, value in [('--cpu', '2'), ('--exhaustiveness', '8'), ('--num_modes', '9'),
                            ('--energy_range', '3.0'), ('--config', 'c'), ('--out', 'o')]:
            assert vina[vina.index(flag) + 1] == value

    def test_default_is_pso(self, runner, temp_dir, mock_subprocess):
        receptor, ligands, output_dir = self._files(temp_dir)
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        result = runner.run_moldina_docking(receptor, ligands, output_dir, (0, 0, 0), (20, 20, 20))

        assert '--search_alg_PSO' in mock_subprocess.run.call_args[0][0]
        assert result["docking_algorithm"] == "moldina"
        params = Path(output_dir, "parameters.txt").read_text()
        assert "docking_algorithm = moldina" in params
        assert "population = 500" in params
        assert "search_alg_PSO" in params

    def test_vina_mode_end_to_end(self, runner, temp_dir, mock_subprocess):
        receptor, ligands, output_dir = self._files(temp_dir)
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        result = runner.run_moldina_docking(
            receptor, ligands, output_dir, (0, 0, 0), (20, 20, 20), docking_algorithm="vina",
        )

        cmd = mock_subprocess.run.call_args[0][0]
        assert '--search_alg_PSO' not in cmd and '--population' not in cmd
        assert result["docking_algorithm"] == "vina"
        params = Path(output_dir, "parameters.txt").read_text()
        assert "docking_algorithm = vina" in params
        assert "population" not in params
        assert "search_alg_PSO" not in params

    def test_alias_is_normalised(self, runner, temp_dir, mock_subprocess):
        receptor, ligands, output_dir = self._files(temp_dir)
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        result = runner.run_moldina_docking(
            receptor, ligands, output_dir, (0, 0, 0), (20, 20, 20), docking_algorithm="AutoDock Vina",
        )

        assert result["docking_algorithm"] == "vina"

    def test_unknown_algorithm_raises_before_anything_runs(self, runner, temp_dir, mock_subprocess):
        receptor, ligands, output_dir = self._files(temp_dir)

        with pytest.raises(ValueError, match="Unknown docking_algorithm 'gnina'"):
            runner.run_moldina_docking(
                receptor, ligands, output_dir, (0, 0, 0), (20, 20, 20), docking_algorithm="gnina",
            )

        assert not mock_subprocess.run.called
        assert not os.path.exists(output_dir)

    @pytest.mark.parametrize("algorithm,cpu,expected_workers", [
        ("vina", 1, 8), ("vina", 2, 4), ("vina", 4, 2), ("vina", 8, 1),
        # PSO ignores --cpu (Stage 15.1 finding 2), so cpu must not cut its
        # workers (Review Stage R1, Phase 15, correctness #2).
        ("moldina", 4, 8), ("moldina", 8, 8),
    ])
    def test_workers_clamped_to_quota_divided_by_cpus_per_ligand(self, runner, temp_dir, mock_subprocess,
                                                                 algorithm, cpu, expected_workers):
        """workers × CPUs per ligand never exceeds the container's CPU budget (finding 3)."""
        receptor, ligands, output_dir = self._files(temp_dir, n_ligands=10)
        mock_subprocess.run.return_value = Mock(returncode=0, stdout='', stderr='')

        with patch('services.moldina_runner.ThreadPoolExecutor', wraps=ThreadPoolExecutor) as mock_pool, \
                patch('services.moldina_runner._detect_effective_cpu_count', return_value=8):
            result = runner.run_moldina_docking(
                receptor, ligands, output_dir, (0, 0, 0), (20, 20, 20),
                cpu=cpu, parallel=True, max_workers=8, docking_algorithm=algorithm,
            )

        # Reported, so the benchmark labels its rows with what ran (correctness #7).
        assert result["workers"] == expected_workers
        if expected_workers > 1:
            mock_pool.assert_called_once_with(max_workers=expected_workers)
        else:
            mock_pool.assert_not_called()  # one worker runs the sequential loop
