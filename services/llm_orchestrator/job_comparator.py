"""
Job Result Comparison

Reads workflow_results.json from completed job directories and compares
binding affinities across them in a ligand x job table.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.docking_algorithm import docking_algorithm_of


def _load_results(job_dir: str) -> Dict[str, Any]:
    """Read a job directory's workflow_results.json."""
    results_file = Path(job_dir) / "workflow_results.json"
    if not results_file.exists():
        raise FileNotFoundError(f"No results found in {job_dir}")
    return json.loads(results_file.read_text())


def load_job_affinities(job_dir: str) -> Dict[str, Optional[float]]:
    """Read workflow_results.json from a job directory; return {ligand_id: best_affinity}."""
    return _affinities(_load_results(job_dir))


def _affinities(data: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """{ligand_id: best_affinity} from a workflow_results.json dict.

    A job can dock the same ligand_id more than once (e.g. "two WWO ligands"),
    producing multiple dock-result entries with an identical ligand_id. Each
    occurrence after the first is disambiguated as "WWO #2", "WWO #3", etc. (in
    dock-result order) so no result is silently dropped.
    """
    dock_results = data.get("results", {}).get("dock", {}).get("results", [])

    affinities: Dict[str, Optional[float]] = {}
    seen_counts: Dict[str, int] = {}
    for r in dock_results:
        ligand_id = r["ligand_id"]
        seen_counts[ligand_id] = seen_counts.get(ligand_id, 0) + 1
        occurrence = seen_counts[ligand_id]
        key = ligand_id if occurrence == 1 else f"{ligand_id} #{occurrence}"
        affinities[key] = r.get("best_affinity")

    return affinities


def compare_jobs(job_dirs: List[str]) -> Dict[str, Any]:
    """
    Compare binding affinities across multiple completed job runs.

    Returns:
        {
          "ligands": sorted list of all ligand IDs across all jobs,
          "jobs": list of job display names (user-supplied job_name, falling
                   back to the job directory basename when unnamed; a name
                   shared by several jobs gets each one's directory
                   basename appended, so every column is distinct),
          "algorithms": docking_algorithm per job, aligned with "jobs",
          "affinities": list-of-lists [ligand_idx][job_idx] = float|None,
          "best_per_ligand": {ligand_id: (job_name, affinity)}
        }
    """
    if not job_dirs:
        raise ValueError("At least one job directory is required")

    per_job_data = [_load_results(d) for d in job_dirs]
    job_names = [data.get("job_name") or Path(d).name for d, data in zip(job_dirs, per_job_data)]
    # Two sessions named alike would share a column label: the GUI's table
    # then read a pair of values as one cell and failed, and the best job
    # per ligand couldn't say which of them it was.
    shared = {name for name in job_names if job_names.count(name) > 1}
    job_names = [
        f"{name} ({Path(d).name})" if name in shared else name for name, d in zip(job_names, job_dirs)
    ]
    algorithms = [docking_algorithm_of(data.get("results", {}).get("dock")) for data in per_job_data]
    per_job_affinities = [_affinities(data) for data in per_job_data]

    ligands = sorted({ligand_id for affinities in per_job_affinities for ligand_id in affinities})

    affinity_table: List[List[Optional[float]]] = []
    best_per_ligand: Dict[str, Tuple[str, float]] = {}

    for ligand_id in ligands:
        row = [affinities.get(ligand_id) for affinities in per_job_affinities]
        affinity_table.append(row)

        candidates = [(job_names[i], val) for i, val in enumerate(row) if val is not None]
        if candidates:
            # Most negative kcal/mol = strongest predicted binding.
            best_per_ligand[ligand_id] = min(candidates, key=lambda pair: pair[1])

    return {
        "ligands": ligands,
        "jobs": job_names,
        "algorithms": algorithms,
        "affinities": affinity_table,
        "best_per_ligand": best_per_ligand,
    }
