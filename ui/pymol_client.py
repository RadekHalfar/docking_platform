"""HTTP client for the pymol-interactive helper API (load_job + status).

Extracted out of ui/app.py so this logic is unit-testable — ui/app.py itself
executes top-level Streamlit calls at import time, so nothing defined
directly in it can be imported/tested in isolation.
"""
from typing import Any, Dict

import requests

_KICKOFF_TIMEOUT = 10  # /api/load_job now returns almost immediately (it just starts a background thread)
_STATUS_TIMEOUT = 10   # /api/load_status is a trivial in-memory read


def kickoff_load_job(api_url: str, job_id: str) -> Dict[str, Any]:
    """POST /api/load_job. Never raises — connection/timeout errors are
    returned as {"status": "error", "error": ...} so callers don't need
    their own try/except."""
    try:
        resp = requests.post(
            f"{api_url}/api/load_job", json={"job_id": job_id}, timeout=_KICKOFF_TIMEOUT
        )
        if resp.ok:
            return resp.json()
        return {"status": "error", "error": f"load_job failed ({resp.status_code}): {resp.text[:200]}"}
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Cannot reach PyMOL helper API: {exc}"}


def get_load_status(api_url: str) -> Dict[str, Any]:
    """GET /api/load_status. Never raises — see kickoff_load_job."""
    try:
        resp = requests.get(f"{api_url}/api/load_status", timeout=_STATUS_TIMEOUT)
        if resp.ok:
            return resp.json()
        return {"status": "error", "error": f"load_status failed ({resp.status_code}): {resp.text[:200]}"}
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Cannot reach PyMOL helper API: {exc}"}
