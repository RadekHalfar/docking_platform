"""
Job Result Comparison

Reads workflow_results.json from completed job directories and compares
binding affinities across them in a ligand x job table.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_job_affinities(job_dir: str) -> Dict[str, Optional[float]]:
    """Read workflow_results.json from a job directory; return {ligand_id: best_affinity}.

    A job can dock the same ligand_id more than once (e.g. "two WWO ligands"),
    producing multiple dock-result entries with an identical ligand_id. Each
    occurrence after the first is disambiguated as "WWO #2", "WWO #3", etc. (in
    dock-result order) so no result is silently dropped.
    """
    results_file = Path(job_dir) / "workflow_results.json"
    if not results_file.exists():
        raise FileNotFoundError(f"No results found in {job_dir}")

    data = json.loads(results_file.read_text())
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
          "jobs": list of job directory basenames,
          "affinities": list-of-lists [ligand_idx][job_idx] = float|None,
          "best_per_ligand": {ligand_id: (job_name, affinity)}
        }
    """
    if not job_dirs:
        raise ValueError("At least one job directory is required")

    job_names = [Path(d).name for d in job_dirs]
    per_job_affinities = [load_job_affinities(d) for d in job_dirs]

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
        "affinities": affinity_table,
        "best_per_ligand": best_per_ligand,
    }
