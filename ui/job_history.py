"""List completed docking jobs for the sidebar job-history / comparison picker.

No Streamlit dependency — kept pure so it can be unit tested directly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

_MD_SPECIAL_RE = re.compile(r'([\\`*_\[\]#])')


def escape_markdown(text: str) -> str:
    """Escape characters Streamlit's markdown renderer treats specially.

    Used for user-supplied job names interpolated into markdown-rendered
    labels/captions, so e.g. a name containing "**" can't visually break
    the surrounding bold/formatting.
    """
    return _MD_SPECIAL_RE.sub(r'\\\1', text)


def list_completed_jobs(output_base: str) -> List[Dict[str, Any]]:
    """Scan output_base for completed jobs.

    Returns a list of {job_id, job_dir, job_name, protein_id, ligand_count}
    dicts, sorted newest first (job IDs are timestamp-prefixed, e.g. job_20260701_...).
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
                "job_name": data.get("job_name", ""),
                "protein_id": protein_id,
                "ligand_count": len(dock.get("results", [])),
            }
        )

    return sorted(jobs, key=lambda j: j["job_id"], reverse=True)


def rename_job(job_dir: str, new_name: str) -> None:
    """Update the job_name field of a completed job's workflow_results.json in place."""
    results_file = Path(job_dir) / "workflow_results.json"
    data = json.loads(results_file.read_text())
    data["job_name"] = new_name.strip()[:60]
    results_file.write_text(json.dumps(data, indent=2))
