"""List completed docking jobs for the sidebar job-history / comparison picker.

No Streamlit dependency — kept pure so it can be unit tested directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def list_completed_jobs(output_base: str) -> List[Dict[str, Any]]:
    """Scan output_base for completed jobs.

    Returns a list of {job_id, job_dir, protein_id, ligand_count} dicts,
    sorted newest first (job IDs are timestamp-prefixed, e.g. job_20260701_...).
    """
    base = Path(output_base)
    if not base.exists():
        return []

    jobs: List[Dict[str, Any]] = []
    for results_file in base.glob("*/workflow_results.json"):
        try:
            data = json.loads(results_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("status") != "completed":
            continue

        get_input_files = data.get("results", {}).get("get_input_files", {})
        dock = data.get("results", {}).get("dock", {})

        protein_path = get_input_files.get("protein_path")
        protein_id = Path(protein_path).stem if protein_path else "?"

        jobs.append(
            {
                "job_id": results_file.parent.name,
                "job_dir": str(results_file.parent),
                "protein_id": protein_id,
                "ligand_count": len(dock.get("results", [])),
            }
        )

    return sorted(jobs, key=lambda j: j["job_id"], reverse=True)
