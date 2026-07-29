"""
Moldina Runner Service

This service runs Moldina for molecular docking calculations using WSL on Windows.
Moldina is an advanced molecular docking tool using Particle Swarm Optimization (PSO)
built on top of AutoDock Vina.

On Windows: Uses WSL (Windows Subsystem for Linux) to run Moldina
On Linux: Runs Moldina directly

Tool: run_docking
- Performs molecular docking with Moldina (PSO-based optimization)
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
import json
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import Dict, List, Optional, Any, Tuple

from common.utils import ensure_directory, compute_file_checksum

logger = logging.getLogger(__name__)


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
    
    def _wsl_path_to_windows(self, wsl_path: str) -> str:
        """
        Convert WSL path to Windows path.
        
        Args:
            wsl_path: WSL path (e.g., /mnt/c/Users/...)
            
        Returns:
            Windows path (e.g., C:\\Users\\...)
        """
        if wsl_path.startswith('/mnt/'):
            # /mnt/c/Users/... -> C:\Users\...
            parts = wsl_path[5:].split('/')  # Remove '/mnt/' and split
            drive = parts[0].upper()
            path_parts = parts[1:]
            windows_path = f"{drive}:\\" + "\\".join(path_parts)
            return windows_path
        return wsl_path
    
    def _run_wsl_command(self, command: List[str], timeout: int = 3600) -> subprocess.CompletedProcess:
        """
        Run command in WSL.
        
        Args:
            command: Command and arguments to run
            timeout: Timeout in seconds
            
        Returns:
            CompletedProcess result
        """
        wsl_cmd = ['wsl', '-d', self.wsl_distro, '--'] + command
        logger.info(f"Running WSL command: {' '.join(wsl_cmd)}")
        
        result = subprocess.run(
            wsl_cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return result
    
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
        energy_range: float = 100.0
    ) -> Dict[str, Any]:
        """
        Run Moldina docking.
        
        Args:
            receptor_path: Path to receptor PDBQT file
            ligand_paths: List of paths to ligand PDBQT files
            output_dir: Directory for output files
            center: (x, y, z) coordinates of search box center
            box_size: (x, y, z) dimensions of search box in Angstroms
            cpu: Number of CPUs to use
            population: Population size for PSO (minimum 5)
            exhaustiveness: Search exhaustiveness
            num_modes: Maximum number of binding modes
            energy_range: Maximum energy difference for modes (kcal/mol)
            
        Returns:
            Dictionary with docking results

        Note: ligands are docked one at a time (one Moldina invocation per ligand)
        rather than batched into a single invocation. This keeps memory usage flat
        regardless of batch size and stops one bad ligand from killing the whole
        run — batching all ligands into a single process previously triggered the
        Linux OOM killer (SIGKILL / exit -9) once the container's memory limit was
        exceeded.
        """
        ensure_directory(output_dir)

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
            f.write("# Moldina run parameters\n")
            f.write(f"receptor = {receptor_wsl}\n")
            for lig in ligand_paths:
                f.write(f"ligand = {lig}\n")
            f.write(f"config = {config_wsl}\n")
            f.write(f"cpu = {cpu}\n")
            f.write(f"population = {population}\n")
            f.write(f"exhaustiveness = {exhaustiveness}\n")
            f.write(f"num_modes = {num_modes}\n")
            f.write(f"energy_range = {energy_range}\n")
            f.write(f"search_alg_PSO\n")

        logger.info(f"Running Moldina docking for {len(ligand_paths)} ligand(s), one at a time")

        results: List[Dict[str, Any]] = []
        errors: List[str] = []

        total = len(ligand_paths)
        completed = 0
        _write_docking_progress(output_dir, {
            "total": total,
            "completed": 0,
            "current_ligand": None,
            "current_index": 0,
            "status": "running",
            "current_started_at": None,
        })

        for idx, ligand_path in enumerate(ligand_paths, start=1):
            ligand_id = Path(ligand_path).stem

            _write_docking_progress(output_dir, {
                "total": total,
                "completed": completed,
                "current_ligand": ligand_id,
                "current_index": idx,
                "status": "running",
                "current_started_at": datetime.now(timezone.utc).isoformat(),
            })

            if self.is_windows:
                ligand_wsl = self._windows_path_to_wsl(ligand_path)
            else:
                ligand_wsl = os.path.abspath(ligand_path)

            output_prefix = os.path.join(output_dir, f"{ligand_id}_out.pdbqt")
            if self.is_windows:
                output_prefix_wsl = self._windows_path_to_wsl(output_prefix)
            else:
                output_prefix_wsl = os.path.abspath(output_prefix)

            moldina_cmd = [
                self.moldina_path,
                '--receptor', receptor_wsl,
                '--ligand', ligand_wsl,
                '--config', config_wsl,
                '--out', output_prefix_wsl,
                '--cpu', str(cpu),
                '--population', str(population),
                '--exhaustiveness', str(exhaustiveness),
                '--num_modes', str(num_modes),
                '--energy_range', str(energy_range),
                '--search_alg_PSO',
            ]

            try:
                if self.is_windows:
                    moldina_cmd_str = ' '.join([shlex.quote(arg) for arg in moldina_cmd])
                    result = self._run_wsl_command(['bash', '-lc', moldina_cmd_str])
                else:
                    result = subprocess.run(
                        moldina_cmd,
                        capture_output=True,
                        text=True,
                        timeout=3600
                    )
            except subprocess.TimeoutExpired:
                logger.error(f"Moldina timed out for {ligand_id} (>1 hour)")
                errors.append(f"{ligand_id}: docking timed out (>1 hour)")
                completed += 1
                _write_docking_progress(output_dir, {
                    "total": total,
                    "completed": completed,
                    "current_ligand": ligand_id,
                    "current_index": idx,
                    "status": "running",
                    "current_started_at": None,
                })
                continue

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
                errors.append(
                    f"{ligand_id}: exit {result.returncode}: "
                    f"{result.stderr or result.stdout or '(no output captured)'}"
                )
                completed += 1
                _write_docking_progress(output_dir, {
                    "total": total,
                    "completed": completed,
                    "current_ligand": ligand_id,
                    "current_index": idx,
                    "status": "running",
                    "current_started_at": None,
                })
                continue

            results.append(self._build_ligand_result(ligand_path, output_dir, result.stdout))
            completed += 1
            _write_docking_progress(output_dir, {
                "total": total,
                "completed": completed,
                "current_ligand": ligand_id,
                "current_index": idx,
                "status": "running",
                "current_started_at": None,
            })

        _write_docking_progress(output_dir, {
            "total": total,
            "completed": completed,
            "current_ligand": None,
            "current_index": total,
            "status": "done",
            "current_started_at": None,
        })

        if not results:
            raise RuntimeError(
                "Moldina execution failed for all ligands: " + "; ".join(errors)
            )

        if errors:
            logger.warning(f"Moldina docking had {len(errors)} failure(s): {'; '.join(errors)}")

        # Sort by best affinity (most negative = best)
        results.sort(key=lambda x: x['best_affinity'] if x['best_affinity'] is not None else float('inf'))

        return {
            "status": "ok",
            "receptor": receptor_path,
            "total": len(ligand_paths),
            "successful": len(results),
            "failed": len(ligand_paths) - len(results),
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

    def _parse_moldina_results(self, output_dir: str, ligand_paths: List[str], stdout: str = "") -> List[Dict[str, Any]]:
        """
        Parse Moldina output results.
        
        Args:
            output_dir: Output directory with results
            ligand_paths: List of input ligand paths
            stdout: Moldina stdout containing affinity table
            
        Returns:
            List of results dictionaries
        """
        results = []
        
        # Check if Moldina created a single batch output file
        # When using "out" parameter with ligands list, Moldina creates one file
        batch_output = os.path.join(output_dir, "docking_output.pdbqt")
        batch_log = os.path.join(output_dir, "docking_output.pdbqt.log")
        
        if os.path.exists(batch_output):
            # Moldina batched all ligands into one file
            # Parse the single output file for all ligands
            logger.info(f"Found batch output file: {batch_output}")
            
            # Parse affinity data from stdout
            affinity_data = self._parse_moldina_stdout(stdout)
            
            # If no stdout, try log file
            if not affinity_data["modes"] and os.path.exists(batch_log):
                affinity_data = self._parse_vina_log(batch_log)
            
            results.append({
                "ligand_id": "batch",
                "ligand_path": ", ".join(ligand_paths),
                "output_path": batch_output,
                "log_path": os.path.join(output_dir, "moldina_output.log") if os.path.exists(os.path.join(output_dir, "moldina_output.log")) else None,
                "best_affinity": affinity_data.get("best_affinity"),
                "modes": affinity_data.get("modes", []),
                "num_modes_found": affinity_data.get("num_modes_found", 0),
                "checksum": compute_file_checksum(batch_output)
            })
            
            return results
        
        # Otherwise, check for individual ligand output files
        for ligand_path in ligand_paths:
            ligand_id = Path(ligand_path).stem
            ligand_dir = os.path.dirname(ligand_path)
            
            # Moldina creates output files with the pattern: <out_prefix>_<ligand_name>_out.pdbqt
            # Check for common output patterns in multiple locations
            possible_outputs = [
                # With the "docking" prefix we set in config
                os.path.join(output_dir, f"docking_{ligand_id}_out.pdbqt"),
                # Standard patterns in output directory
                os.path.join(output_dir, f"{ligand_id}_out.pdbqt"),
                os.path.join(output_dir, f"{ligand_id}.pdbqt"),
                os.path.join(output_dir, ligand_id, f"{ligand_id}_out.pdbqt"),
                # In the same directory as the ligand (Moldina default behavior)
                os.path.join(ligand_dir, f"{ligand_id}_out.pdbqt"),
                os.path.join(ligand_dir, f"{ligand_id}.pdbqt"),
            ]
            
            output_file = None
            for path in possible_outputs:
                if os.path.exists(path):
                    output_file = path
                    logger.info(f"Found output file for {ligand_id}: {path}")
                    break
            
            if not output_file:
                logger.warning(f"Output file not found for {ligand_id}. Checked: {possible_outputs}")
                continue
            
            # Look for log file
            log_file = output_file.replace('.pdbqt', '.log')
            if not os.path.exists(log_file):
                log_file = os.path.join(output_dir, f"{ligand_id}.log")
            
            # Parse binding affinity from log if available
            best_affinity = None
            modes = []
            
            # First try to parse from stdout (most reliable)
            if stdout:
                affinity_data = self._parse_moldina_stdout(stdout)
                best_affinity = affinity_data.get("best_affinity")
                modes = affinity_data.get("modes", [])
            
            # If no data from stdout, try log file
            if not modes:
                if os.path.exists(log_file):
                    affinity_data = self._parse_vina_log(log_file)
                    best_affinity = affinity_data.get("best_affinity")
                    modes = affinity_data.get("modes", [])
                # Also try the main moldina_output.log
                elif os.path.exists(os.path.join(output_dir, "moldina_output.log")):
                    affinity_data = self._parse_vina_log(os.path.join(output_dir, "moldina_output.log"))
                    best_affinity = affinity_data.get("best_affinity")
                    modes = affinity_data.get("modes", [])
            
            results.append({
                "ligand_id": ligand_id,
                "ligand_path": ligand_path,
                "output_path": output_file,
                "log_path": log_file if os.path.exists(log_file) else os.path.join(output_dir, "moldina_output.log"),
                "best_affinity": best_affinity,
                "modes": modes,
                "num_modes_found": len(modes),
                "checksum": compute_file_checksum(output_file)
            })
        
        # Sort by best affinity (most negative = best)
        results.sort(key=lambda x: x['best_affinity'] if x['best_affinity'] is not None else float('inf'))
        
        return results
    
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
