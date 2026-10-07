"""
Moldina Runner Service

This service runs Moldina for molecular docking calculations using WSL on Windows.
Moldina is an advanced molecular docking tool using Particle Swarm Optimization (PSO)
built on top of AutoDock Vina. The same binary also runs the classic Vina
(1.1.2-era) Monte Carlo search when the PSO flags are omitted -- selected
with ``docking_algorithm`` (see ``common/docking_algorithm.py``).

On Windows: Uses WSL (Windows Subsystem for Linux) to run Moldina
On Linux: Runs Moldina directly

Tool: run_docking
- Performs molecular docking with Moldina (PSO, default) or Vina (Monte Carlo)
- Requires prepared PDBQT files for receptor and ligands
- Returns docking results with binding affinities and poses
"""
from __future__ import annotations
import os
import shlex
import re
import platform
import subprocess
import logging
import shutil
import tempfile
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable, Dict, List, Optional, Any, Tuple

from common.docking_algorithm import (
    DEFAULT_DOCKING_ALGORITHM,
    DOCKING_ALGORITHMS,
    cpus_per_ligand,
    normalize_docking_algorithm,
)
from common.docking_stop import docking_stop_requested
from common.utils import ensure_directory, compute_file_checksum

logger = logging.getLogger(__name__)


def _read_cgroup_cpu_quota() -> Optional[int]:
    """Read the configured cgroup CPU quota (v2 first, then v1) as a whole
    number of cores, or None if no quota is enforced / the files aren't
    readable (e.g. running outside a container)."""
    # cgroup v2: single file "<quota> <period>" in microseconds, or
    # "max <period>" if no quota is enforced.
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota_str, period_str = f.read().split()
        if quota_str != "max":
            quota, period = int(quota_str), int(period_str)
            if quota > 0 and period > 0:
                return quota // period
    except (OSError, ValueError):
        pass

    # cgroup v1: separate quota/period files; quota is -1 when unlimited.
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") as f:
            quota = int(f.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as f:
            period = int(f.read().strip())
        if quota > 0 and period > 0:
            return quota // period
    except (OSError, ValueError):
        pass

    return None


def _detect_effective_cpu_count() -> int:
    """Best-effort count of CPUs actually schedulable by this process/container.

    os.cpu_count() reports how many logical CPUs the OS exposes, but a
    Docker Compose `deploy.resources.limits.cpus` limit is enforced via a
    cgroup CFS bandwidth quota, not a CPU affinity mask — os.cpu_count()
    does not reflect it at all (confirmed in practice: a container limited
    to cpus: '4' still reports the host's full core count).

    Conversely, the configured quota alone isn't trustworthy either: the
    same compose file might be deployed on smaller hardware than the quota
    assumes (e.g. cpus: '10' run on a 4-core machine), where the quota
    simply can't bind and the physical/logical core count is the real
    ceiling. So this takes whichever of the two is smaller — never trusting
    either number alone — and falls back to os.cpu_count() outright when no
    quota is configured at all.
    """
    os_count = os.cpu_count() or 1
    quota = _read_cgroup_cpu_quota()
    if quota is None:
        return os_count
    return max(1, min(quota, os_count))


# (min, max) bounds for each run_docking numeric parameter. Values outside
# these are clamped rather than rejected -- an LLM-parsed or slightly
# out-of-range request shouldn't hard-fail a whole job, it should just not
# be allowed to request pathological resource use or nonsensical Moldina
# CLI arguments.
_POPULATION_BOUNDS = (5, 5000)  # docstring's documented minimum is 5
_EXHAUSTIVENESS_BOUNDS = (1, 64)
_NUM_MODES_BOUNDS = (1, 50)
_ENERGY_RANGE_BOUNDS = (0.1, 1000.0)


def _clamp_docking_params(
    cpu: int,
    population: int,
    exhaustiveness: int,
    num_modes: int,
    energy_range: float,
) -> Tuple[int, int, int, int, float]:
    """Clamp run_docking's numeric parameters to sane bounds.

    cpu is clamped to this container's real schedulable CPU budget (see
    _detect_effective_cpu_count()) the same way max_workers already is --
    requesting more than that just contends with itself for no benefit.
    """
    clamped_cpu = max(1, min(int(cpu), _detect_effective_cpu_count()))
    clamped_population = max(_POPULATION_BOUNDS[0], min(int(population), _POPULATION_BOUNDS[1]))
    clamped_exhaustiveness = max(_EXHAUSTIVENESS_BOUNDS[0], min(int(exhaustiveness), _EXHAUSTIVENESS_BOUNDS[1]))
    clamped_num_modes = max(_NUM_MODES_BOUNDS[0], min(int(num_modes), _NUM_MODES_BOUNDS[1]))
    clamped_energy_range = max(_ENERGY_RANGE_BOUNDS[0], min(float(energy_range), _ENERGY_RANGE_BOUNDS[1]))

    clamped = (clamped_cpu, clamped_population, clamped_exhaustiveness, clamped_num_modes, clamped_energy_range)
    requested = (cpu, population, exhaustiveness, num_modes, energy_range)
    if clamped != requested:
        logger.warning(
            "run_docking parameters clamped: requested cpu=%s population=%s exhaustiveness=%s "
            "num_modes=%s energy_range=%s -> using cpu=%s population=%s exhaustiveness=%s "
            "num_modes=%s energy_range=%s",
            *requested, *clamped,
        )
    return clamped


def _build_docking_command(
    moldina_path: str,
    receptor: str,
    ligand: str,
    config: str,
    out: str,
    *,
    algorithm: str,
    cpu: int,
    population: int,
    exhaustiveness: int,
    num_modes: int,
    energy_range: float,
) -> List[str]:
    """Build one ligand's Moldina command line.

    ``algorithm`` is a canonical name from ``DOCKING_ALGORITHMS``. PSO
    ("moldina") adds ``--population`` and ``--search_alg_PSO``; without
    them the binary runs Vina's Monte Carlo search ("vina"). The remaining
    flags are shared by both modes.
    """
    cmd = [
        moldina_path,
        '--receptor', receptor,
        '--ligand', ligand,
        '--config', config,
        '--out', out,
        '--cpu', str(cpu),
        '--exhaustiveness', str(exhaustiveness),
        '--num_modes', str(num_modes),
        '--energy_range', str(energy_range),
    ]
    if algorithm == "moldina":
        cmd += ['--population', str(population), '--search_alg_PSO']
    return cmd


def _write_docking_progress(output_dir: str, payload: Dict[str, Any]) -> None:
    """Atomically write docking_progress.json so readers never see a partial write."""
    path = os.path.join(output_dir, "docking_progress.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"Failed to write docking progress file: {e}")


@dataclass
class _ProcessResult:
    """Outcome of one finished Moldina process (what subprocess.run used to return)."""

    returncode: int
    stdout: str
    stderr: str


# How often a running Moldina process checks for a stop request (seconds).
_STOP_POLL_INTERVAL = 0.5
# Grace period between SIGTERM and SIGKILL when stopping a process (seconds).
_STOP_KILL_GRACE = 5


def _run_cancellable(
    cmd: List[str],
    stop_check: Callable[[], bool],
    timeout: float = 3600,
) -> Optional[_ProcessResult]:
    """Run *cmd* to completion, unless *stop_check()* turns true first.

    Polls every ``_STOP_POLL_INTERVAL`` seconds. On a stop request the
    process is terminated (then killed after ``_STOP_KILL_GRACE`` seconds)
    and ``None`` is returned. Raises ``subprocess.TimeoutExpired`` after
    *timeout* seconds, like ``subprocess.run(timeout=...)`` did.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=_STOP_POLL_INTERVAL)
            return _ProcessResult(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            pass
        if stop_check():
            proc.terminate()
            try:
                proc.communicate(timeout=_STOP_KILL_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            return None
        if time.monotonic() >= deadline:
            proc.kill()
            proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout)


class _DockingProgress:
    """Thread-safe tracker for docking_progress.json.

    Supports any number of ligands in flight at once (sequential docking is
    just the special case where at most one is ever "in progress"), guarding
    the shared in-memory state and the on-disk snapshot with one lock so
    concurrent starts/finishes from multiple worker threads never race or
    clobber each other.
    """

    def __init__(self, output_dir: str, total: int):
        self._output_dir = output_dir
        self._total = total
        self._completed = 0
        self._in_progress: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._write_snapshot("running")

    def _write_snapshot(self, status: str) -> None:
        payload = {
            "total": self._total,
            "completed": self._completed,
            "status": status,
            "in_progress": [
                {"ligand": ligand_id, **info}
                for ligand_id, info in self._in_progress.items()
            ],
        }
        _write_docking_progress(self._output_dir, payload)

    def start(self, ligand_id: str, index: int) -> None:
        with self._lock:
            self._in_progress[ligand_id] = {
                "index": index,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_snapshot("running")

    def finish(self, ligand_id: str) -> None:
        with self._lock:
            self._in_progress.pop(ligand_id, None)
            self._completed += 1
            self._write_snapshot("running")

    def abandon(self, ligand_id: str) -> None:
        """Drop a ligand that was stopped mid-docking (not counted as completed)."""
        with self._lock:
            self._in_progress.pop(ligand_id, None)
            self._write_snapshot("running")

    def done(self) -> None:
        with self._lock:
            self._in_progress.clear()
            self._write_snapshot("done")

    def stopped(self) -> None:
        with self._lock:
            self._in_progress.clear()
            self._write_snapshot("stopped")


class MoldinaRunner:
    """
    Moldina runner for molecular docking with PSO optimization.
    
    Supports:
    - Windows: Uses WSL (Windows Subsystem for Linux) to run Moldina
    - Linux: Runs Moldina directly
    """
    
    def __init__(self, cache_dir: Optional[str] = None, moldina_path: Optional[str] = None, 
                 wsl_distro: str = "Ubuntu"):
        """
        Initialize Moldina Runner.
        
        Args:
            cache_dir: Directory for caching results
            moldina_path: Path to Moldina executable in WSL/Linux (auto-detected if None)
            wsl_distro: WSL distribution name (default: Ubuntu)
        """
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".docking_cache" / "moldina"
        ensure_directory(str(self.cache_dir))
        
        self.is_windows = platform.system() == "Windows"
        self.wsl_distro = wsl_distro
        
        # Check WSL availability on Windows
        if self.is_windows:
            if not self._check_wsl():
                raise RuntimeError("WSL not found. Please install WSL to use Moldina on Windows.")
            logger.info(f"Using WSL distribution: {self.wsl_distro}")
        
        # Detect Moldina executable
        self.moldina_path = self._find_moldina_executable(moldina_path)
        if not self.moldina_path:
            raise RuntimeError("Moldina not found. Please install Moldina in WSL/Linux.")
        
        logger.info(f"Moldina executable: {self.moldina_path}")
    
    def _check_wsl(self) -> bool:
        """
        Check if WSL is available on Windows.
        
        Returns:
            True if WSL is available, False otherwise
        """
        try:
            result = subprocess.run(['wsl', '--list'], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"WSL check failed: {e}")
            return False
    
    def _find_moldina_executable(self, custom_path: Optional[str] = None) -> Optional[str]:
        """
        Find Moldina executable in WSL/Linux.
        
        Args:
            custom_path: Custom path to Moldina executable
            
        Returns:
            Path to Moldina executable (Linux path) or None if not found
        """
        if custom_path:
            return custom_path
        
        # Common Moldina installation paths (Linux paths)
        common_paths = [
            "/home/$USER/Moldina/build/linux/release/Moldina",
            "/home/$USER/Moldina/build/linux/release/moldina",
            "$HOME/Moldina/build/linux/release/Moldina",
            "$HOME/Moldina/build/linux/release/moldina",
            "/usr/local/bin/Moldina",
            "/usr/local/bin/moldina",
            "/usr/bin/Moldina",
            "/usr/bin/moldina",
            "$HOME/.local/bin/Moldina",
            "$HOME/.local/bin/moldina",
        ]
        
        for path in common_paths:
            if self.is_windows:
                # Check if file exists in WSL using bash for env var expansion and &&
                bash_cmd = f"test -f {shlex.quote(path)} && echo exists"
                check_cmd = ['wsl', '-d', self.wsl_distro, '--', 'bash', '-lc', bash_cmd]
                try:
                    result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and 'exists' in result.stdout:
                        return path
                except Exception:
                    continue
            else:
                # Direct check on Linux
                expanded_path = os.path.expandvars(path).replace('$USER', os.environ.get('USER', 'ubuntu'))
                if os.path.exists(expanded_path):
                    return expanded_path
        
        # Try which command
        if self.is_windows:
            try:
                for candidate in ('Moldina', 'moldina'):
                    result = subprocess.run(['wsl', '-d', self.wsl_distro, '--', 'which', candidate],
                                            capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout.strip()
            except Exception:
                pass
        else:
            for candidate in ('Moldina', 'moldina'):
                moldina_path = shutil.which(candidate)
                if moldina_path:
                    return moldina_path
        
        logger.warning("Moldina not found. Please install Moldina in WSL/Linux.")
        return None
    
    def _windows_path_to_wsl(self, windows_path: str) -> str:
        """
        Convert Windows path to WSL path.
        
        Args:
            windows_path: Windows path (e.g., C:\\Users\\...)
            
        Returns:
            WSL path (e.g., /mnt/c/Users/...)
        """
        # Use PureWindowsPath so this works correctly on Linux too
        win_path = PureWindowsPath(windows_path)

        # Convert to WSL format: C:\Users\... -> /mnt/c/Users/...
        drive = win_path.drive[0].lower()  # 'C' from 'C:'
        path_without_drive = win_path.as_posix()[len(win_path.drive):]  # strip 'C:'
        wsl_path = f"/mnt/{drive}{path_without_drive}"
        
        return wsl_path
    
    def _run_wsl_command(
        self,
        command: List[str],
        timeout: int = 3600,
        stop_check: Callable[[], bool] = lambda: False,
    ) -> Optional[_ProcessResult]:
        """
        Run command in WSL.

        Args:
            command: Command and arguments to run
            timeout: Timeout in seconds
            stop_check: Polled while running; when it returns True the
                process is stopped (see ``_run_cancellable``)

        Returns:
            The process result, or None if it was stopped. Note that
            terminating ``wsl.exe`` is not guaranteed to stop the Linux-side
            process (Windows/WSL dev mode only; the Docker deployment runs
            Moldina directly).
        """
        wsl_cmd = ['wsl', '-d', self.wsl_distro, '--'] + command
        logger.info(f"Running WSL command: {' '.join(wsl_cmd)}")

        return _run_cancellable(wsl_cmd, stop_check, timeout=timeout)
    
    def _dock_one_ligand(
        self,
        ligand_path: str,
        index: int,
        receptor_wsl: str,
        config_wsl: str,
        output_dir: str,
        cpu: int,
        population: int,
        exhaustiveness: int,
        num_modes: int,
        energy_range: float,
        progress: "_DockingProgress",
        stop_check: Callable[[], bool] = lambda: False,
        algorithm: str = DEFAULT_DOCKING_ALGORITHM,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
        """
        Dock a single ligand against the already-prepared receptor/config.

        Safe to call concurrently from multiple threads for different ligands
        in the same batch — every path this method touches (output PDBQT, log
        file) is uniquely named per ligand_id, and it shares no mutable state
        with other concurrent calls besides `progress`, which is internally
        locked.

        Returns ``(result, error, stopped)``: ``(result, None, False)`` on
        success, ``(None, error_message, False)`` on failure, and
        ``(None, None, True)`` when a stop was requested -- before this
        ligand started (it is skipped) or while it ran (its Moldina process
        is killed). A stopped ligand is neither a result nor an error.
        """
        ligand_id = Path(ligand_path).stem
        if stop_check():
            return None, None, True
        progress.start(ligand_id, index)

        if self.is_windows:
            ligand_wsl = self._windows_path_to_wsl(ligand_path)
        else:
            ligand_wsl = os.path.abspath(ligand_path)

        output_prefix = os.path.join(output_dir, f"{ligand_id}_out.pdbqt")
        if self.is_windows:
            output_prefix_wsl = self._windows_path_to_wsl(output_prefix)
        else:
            output_prefix_wsl = os.path.abspath(output_prefix)

        moldina_cmd = _build_docking_command(
            self.moldina_path, receptor_wsl, ligand_wsl, config_wsl, output_prefix_wsl,
            algorithm=algorithm, cpu=cpu, population=population,
            exhaustiveness=exhaustiveness, num_modes=num_modes, energy_range=energy_range,
        )

        try:
            if self.is_windows:
                moldina_cmd_str = ' '.join([shlex.quote(arg) for arg in moldina_cmd])
                result = self._run_wsl_command(
                    ['bash', '-lc', moldina_cmd_str], stop_check=stop_check
                )
            else:
                result = _run_cancellable(moldina_cmd, stop_check, timeout=3600)
        except subprocess.TimeoutExpired:
            logger.error(f"Moldina timed out for {ligand_id} (>1 hour)")
            progress.finish(ligand_id)
            return None, f"{ligand_id}: docking timed out (>1 hour)", False

        if result is None:
            logger.info(f"Moldina stopped for {ligand_id} (stop requested)")
            progress.abandon(ligand_id)
            return None, None, True

        # Save stdout/stderr as a per-ligand log file unconditionally, so a
        # failure is still debuggable from disk (the error text is often on
        # stdout, not stderr).
        log_file = os.path.join(output_dir, f"{ligand_id}_moldina_output.log")
        with open(log_file, 'w') as f:
            f.write("=== STDOUT ===\n")
            f.write(result.stdout)
            f.write("\n\n=== STDERR ===\n")
            f.write(result.stderr)

        if result.returncode != 0:
            logger.error(
                f"Moldina failed for {ligand_id} (exit {result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
            progress.finish(ligand_id)
            return None, (
                f"{ligand_id}: exit {result.returncode}: "
                f"{result.stderr or result.stdout or '(no output captured)'}"
            ), False

        ligand_result = self._build_ligand_result(ligand_path, output_dir, result.stdout)
        progress.finish(ligand_id)
        return ligand_result, None, False

    def run_moldina_docking(
        self,
        receptor_path: str,
        ligand_paths: List[str],
        output_dir: str,
        center: Tuple[float, float, float],
        box_size: Tuple[float, float, float],
        cpu: int = 1,
        population: int = 500,
        exhaustiveness: int = 8,
        num_modes: int = 15,
        energy_range: float = 100.0,
        parallel: bool = False,
        max_workers: int = 1,
        docking_algorithm: str = DEFAULT_DOCKING_ALGORITHM,
    ) -> Dict[str, Any]:
        """
        Run Moldina docking.

        Args:
            receptor_path: Path to receptor PDBQT file
            ligand_paths: List of paths to ligand PDBQT files
            output_dir: Directory for output files
            center: (x, y, z) coordinates of search box center
            box_size: (x, y, z) dimensions of search box in Angstroms
            cpu: Number of CPUs to use per ligand
            population: Population size for PSO (minimum 5; unused in Vina mode)
            exhaustiveness: Search exhaustiveness
            num_modes: Maximum number of binding modes
            energy_range: Maximum energy difference for modes (kcal/mol)
            parallel: Dock up to max_workers ligands concurrently instead of
                one at a time (default: False, i.e. sequential)
            max_workers: Maximum number of ligands to dock concurrently when
                parallel is True — clamped to this process's own CPU count
                divided by the CPUs each ligand really uses (``cpu`` in Vina
                mode, 1 in PSO mode, which ignores ``--cpu``; see
                ``cpus_per_ligand``), so workers × CPUs never oversubscribes
                it, and to the number of ligands, whichever is smallest
            docking_algorithm: "moldina" (PSO, default) or "vina" (the
                binary's classic Monte Carlo search); aliases accepted by
                ``normalize_docking_algorithm``. Anything else raises
                ValueError before any file is written.

        Returns:
            Dictionary with docking results, including the canonical
            ``docking_algorithm`` that produced them and ``workers``, the
            number of ligands actually docked at a time after the clamp. ``stopped`` is True when a stop
            was requested mid-run (a ``STOP_REQUESTED`` flag file appeared in
            *output_dir*, see ``common/docking_stop.py``): no further ligands
            were started and running Moldina processes were killed.
            ``status`` is then ``"stopped"`` and ``skipped`` counts the
            ligands that were never docked (not started, or killed).

        Note: ligands are docked one Moldina invocation at a time per worker
        (never batched into a single invocation). This keeps per-invocation
        memory usage flat regardless of batch size and stops one bad ligand
        from killing the whole run — batching all ligands into a single
        process previously triggered the Linux OOM killer (SIGKILL / exit -9)
        once the container's memory limit was exceeded. Running several
        single-ligand invocations concurrently (parallel=True) carries a
        similar-but-smaller memory risk, which is why the worker count is
        opt-in and capped rather than unbounded.
        """
        algorithm = normalize_docking_algorithm(docking_algorithm)
        if algorithm is None:
            raise ValueError(
                f"Unknown docking_algorithm {docking_algorithm!r}; expected one of "
                + ", ".join(repr(a) for a in DOCKING_ALGORITHMS)
            )

        ensure_directory(output_dir)

        cpu, population, exhaustiveness, num_modes, energy_range = _clamp_docking_params(
            cpu, population, exhaustiveness, num_modes, energy_range,
        )

        # Convert Windows paths to WSL paths if needed
        if self.is_windows:
            receptor_wsl = self._windows_path_to_wsl(receptor_path)
        else:
            receptor_wsl = os.path.abspath(receptor_path)

        # Config file contains only box/center parameters (new Moldina interface)
        config_path = os.path.join(output_dir, "center.txt")
        config_content = f"""center_x = {center[0]}
center_y = {center[1]}
center_z = {center[2]}
size_x = {box_size[0]}
size_y = {box_size[1]}
size_z = {box_size[2]}
"""

        with open(config_path, 'w') as f:
            f.write(config_content)

        if self.is_windows:
            config_wsl = self._windows_path_to_wsl(config_path)
        else:
            config_wsl = os.path.abspath(config_path)

        # Save parameters summary (informational; one Moldina invocation per ligand below)
        params_path = os.path.join(output_dir, "parameters.txt")
        with open(params_path, 'w') as f:
            f.write("# Docking run parameters\n")
            f.write(f"docking_algorithm = {algorithm}\n")
            f.write(f"receptor = {receptor_wsl}\n")
            for lig in ligand_paths:
                f.write(f"ligand = {lig}\n")
            f.write(f"config = {config_wsl}\n")
            f.write(f"cpu = {cpu}\n")
            if algorithm == "moldina":
                f.write(f"population = {population}\n")
            f.write(f"exhaustiveness = {exhaustiveness}\n")
            f.write(f"num_modes = {num_modes}\n")
            f.write(f"energy_range = {energy_range}\n")
            if algorithm == "moldina":
                f.write("search_alg_PSO\n")

        results: List[Dict[str, Any]] = []
        errors: List[str] = []
        stopped_count = 0
        results_lock = threading.Lock()

        def _stop_check() -> bool:
            return docking_stop_requested(output_dir)

        def _record(ligand_result: Optional[Dict[str, Any]], error: Optional[str], stopped: bool) -> None:
            nonlocal stopped_count
            with results_lock:
                if stopped:
                    stopped_count += 1
                elif error is not None:
                    errors.append(error)
                else:
                    results.append(ligand_result)

        # Clamped to this container's real, schedulable CPU budget (not
        # whatever the caller guessed, and not os.cpu_count() — see
        # _detect_effective_cpu_count()'s docstring for why that's unsafe)
        # divided by the CPUs each worker's process really uses, so workers
        # × CPUs never oversubscribes it (Vina's Monte Carlo search is
        # multithreaded; PSO ignores --cpu and uses one), and to the number
        # of ligands — no point spinning up more workers than any of these
        # allows.
        workers = (
            max(1, min(
                max_workers,
                _detect_effective_cpu_count() // cpus_per_ligand(algorithm, cpu),
                len(ligand_paths),
            ))
            if parallel and len(ligand_paths) > 1
            else 1
        )

        logger.info(
            f"Running {algorithm} docking for {len(ligand_paths)} ligand(s), "
            + (f"up to {workers} at a time" if workers > 1 else "one at a time")
        )

        progress = _DockingProgress(output_dir, total=len(ligand_paths))

        if workers <= 1:
            for idx, ligand_path in enumerate(ligand_paths, start=1):
                ligand_result, error, stopped = self._dock_one_ligand(
                    ligand_path, idx, receptor_wsl, config_wsl, output_dir,
                    cpu, population, exhaustiveness, num_modes, energy_range,
                    progress, _stop_check, algorithm,
                )
                _record(ligand_result, error, stopped)
                if stopped:
                    break
        else:
            # Ligands still queued when a stop arrives each see the flag on
            # entry to _dock_one_ligand and return at once without starting.
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        self._dock_one_ligand,
                        ligand_path, idx, receptor_wsl, config_wsl, output_dir,
                        cpu, population, exhaustiveness, num_modes, energy_range,
                        progress, _stop_check, algorithm,
                    )
                    for idx, ligand_path in enumerate(ligand_paths, start=1)
                ]
                for future in as_completed(futures):
                    _record(*future.result())

        was_stopped = stopped_count > 0
        skipped = len(ligand_paths) - len(results) - len(errors)
        if was_stopped:
            progress.stopped()
            logger.info(
                f"Moldina docking stopped on request: {len(results)} docked, "
                f"{len(errors)} failed, {skipped} not docked"
            )
        else:
            progress.done()
            if not results:
                raise RuntimeError(
                    "Moldina execution failed for all ligands: " + "; ".join(errors)
                )

        if errors:
            logger.warning(f"Moldina docking had {len(errors)} failure(s): {'; '.join(errors)}")

        # Sort by best affinity (most negative = best)
        results.sort(key=lambda x: x['best_affinity'] if x['best_affinity'] is not None else float('inf'))

        return {
            "status": "stopped" if was_stopped else "ok",
            "stopped": was_stopped,
            "docking_algorithm": algorithm,
            "workers": workers,
            "skipped": skipped,
            "receptor": receptor_path,
            "total": len(ligand_paths),
            "successful": len(results),
            "failed": len(errors),
            "errors": errors,
            "results": results,
            "config_file": config_path,
            "parameters_file": params_path,
            "output_dir": output_dir,
        }

    def _build_ligand_result(self, ligand_path: str, output_dir: str, stdout: str) -> Dict[str, Any]:
        """
        Build a single ligand's result dict from its own (non-batched) Moldina run.

        Args:
            ligand_path: Path to the input ligand file
            output_dir: Directory containing this ligand's output/log files
            stdout: This ligand's own Moldina stdout (not combined with other ligands)

        Returns:
            Result dictionary for this ligand
        """
        ligand_id = Path(ligand_path).stem
        output_file = os.path.join(output_dir, f"{ligand_id}_out.pdbqt")
        log_file = os.path.join(output_dir, f"{ligand_id}_out.log")

        affinity_data = self._parse_moldina_stdout(stdout)
        if not affinity_data["modes"] and os.path.exists(log_file):
            affinity_data = self._parse_vina_log(log_file)

        return {
            "ligand_id": ligand_id,
            "ligand_path": ligand_path,
            "output_path": output_file if os.path.exists(output_file) else None,
            "log_path": log_file if os.path.exists(log_file) else None,
            "best_affinity": affinity_data.get("best_affinity"),
            "modes": affinity_data.get("modes", []),
            "num_modes_found": affinity_data.get("num_modes_found", 0),
            "checksum": compute_file_checksum(output_file) if os.path.exists(output_file) else None,
        }

    def _parse_vina_log(self, log_path: str) -> Dict[str, Any]:
        """
        Parse Vina-style log file to extract binding affinities.
        
        Args:
            log_path: Path to log file
            
        Returns:
            Dictionary with parsed results
        """
        if not os.path.exists(log_path):
            return {"modes": [], "best_affinity": None, "num_modes_found": 0}
        
        with open(log_path, 'r') as f:
            log_content = f.read()
        
        # Extract binding modes
        modes = []
        mode_pattern = r'^\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
        
        in_results = False
        for line in log_content.split('\n'):
            if 'mode' in line.lower() and 'affinity' in line.lower():
                in_results = True
                continue
            
            if in_results and line.strip():
                match = re.match(mode_pattern, line)
                if match:
                    mode_num, affinity, rmsd_lb, rmsd_ub = match.groups()
                    modes.append({
                        "mode": int(mode_num),
                        "affinity": float(affinity),
                        "rmsd_lb": float(rmsd_lb),
                        "rmsd_ub": float(rmsd_ub)
                    })
        
        # Get best affinity
        best_affinity = modes[0]["affinity"] if modes else None
        
        return {
            "modes": modes,
            "best_affinity": best_affinity,
            "num_modes_found": len(modes)
        }
    
    def _parse_moldina_stdout(self, stdout: str) -> Dict[str, Any]:
        """
        Parse Moldina stdout to extract binding affinities.
        
        Moldina prints a table like:
        mode |   affinity | dist from best mode
             | (kcal/mol) | rmsd l.b.| rmsd u.b.
        -----+------------+----------+----------
           1         -2.1      0.000      0.000
           2         -1.9      7.159     11.300
        
        Args:
            stdout: Moldina stdout text
            
        Returns:
            Dictionary with parsed results
        """
        if not stdout:
            return {"modes": [], "best_affinity": None, "num_modes_found": 0}
        
        modes = []
        # Pattern to match affinity table lines
        # Example: "   1         -2.1      0.000      0.000"
        mode_pattern = r'^\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
        
        in_results = False
        for line in stdout.split('\n'):
            # Look for the table header
            if 'mode' in line.lower() and 'affinity' in line.lower():
                in_results = True
                continue
            
            # Look for table separator
            if in_results and '-----+' in line:
                continue
            
            # Stop at end of table (empty line or "Writing output")
            if in_results and (not line.strip() or 'Writing' in line):
                if modes:  # Only break if we've found some modes
                    break
            
            # Parse mode lines
            if in_results and line.strip():
                match = re.match(mode_pattern, line)
                if match:
                    mode_num, affinity, rmsd_lb, rmsd_ub = match.groups()
                    modes.append({
                        "mode": int(mode_num),
                        "affinity": float(affinity),
                        "rmsd_lb": float(rmsd_lb),
                        "rmsd_ub": float(rmsd_ub)
                    })
        
        # Get best affinity (most negative)
        best_affinity = modes[0]["affinity"] if modes else None
        
        logger.info(f"Parsed {len(modes)} binding modes from Moldina output")
        
        return {
            "modes": modes,
            "best_affinity": best_affinity,
            "num_modes_found": len(modes)
        }
