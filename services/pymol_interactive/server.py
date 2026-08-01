"""Interactive PyMOL helper API for noVNC sessions.

This service does not replace the MCP visualizer; it adds a browser-accessible
interactive PyMOL session and helper endpoints for loading jobs and deriving
Docking Center / Box Size from a visual selection.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xmlrpc.client import ServerProxy

from flask import Flask, jsonify, request

app = Flask(__name__)

OUTPUT_ROOT = Path(os.getenv("ORCHESTRATOR_OUTPUT_DIR", "/app/orchestrator_output")).resolve()
RPC_URL = os.getenv("PYMOL_RPC_URL", "http://127.0.0.1:9123")

# job_id always identifies one existing job directory under OUTPUT_ROOT --
# unlike a user-chosen output path, there's no legitimate reason for it to
# contain path separators or "..", so it's allowlisted rather than escaped.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _resolve_job_dir(job_id: str) -> Path:
    """Resolve job_id to a directory strictly inside OUTPUT_ROOT.

    Raises ValueError for anything that isn't a bare directory-name-shaped
    id, closing off path traversal (e.g. job_id="../../etc").
    """
    if not job_id or not _JOB_ID_RE.match(job_id):
        raise ValueError(f"Invalid job_id: {job_id!r}")
    return OUTPUT_ROOT / job_id


# Serializes every PyMOL command sequence (loading a job, deriving a box from
# a selection) so a background load and a concurrent request can never
# interleave commands into the one live PyMOL session.
_pymol_lock = threading.Lock()

# Guards _load_status only — deliberately a *separate*, fast lock so a status
# poll is never blocked behind an in-flight load (which holds _pymol_lock for
# as long as PyMOL takes to process a whole batch).
_status_lock = threading.Lock()
_load_status: dict[str, Any] = {
    "status": "idle",  # "idle" | "running" | "done" | "error"
    "job_id": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_current_load_thread: threading.Thread | None = None


def _rpc() -> ServerProxy:
    return ServerProxy(RPC_URL, allow_none=True)


def _exists(path_str: str) -> bool:
    try:
        return Path(path_str).exists()
    except Exception:
        return False


def _find_job_assets(job_dir: Path) -> dict[str, Any]:
    prepared_dir = job_dir / "prepared"
    docking_dir = job_dir / "docking_results"

    receptor = None
    receptor_candidates = list(prepared_dir.glob("*.pdbqt")) + list(prepared_dir.glob("*.pdb"))
    if receptor_candidates:
        receptor = str(receptor_candidates[0])

    ligands = []
    docking_candidates = list(docking_dir.glob("*.pdbqt"))
    if docking_candidates:
        ligands = [str(p) for p in docking_candidates]
    else:
        ligands = [str(p) for p in prepared_dir.glob("*.pdbqt") if p.name.lower() != Path(receptor or "").name.lower()]

    return {"receptor_path": receptor, "ligand_paths": ligands}


def _pymol_do(command: str) -> None:
    # PyMOL RPC's 'do' executes command strings in the active GUI session.
    _rpc().do(command)


def _load_into_pymol(receptor_path: str, ligand_paths: list[str]) -> None:
    """Load a receptor + ligand set into the live PyMOL session.

    Issues a single consolidated RPC call (PyMOL's `do()` accepts
    semicolon-separated multi-statement scripts) instead of one call per
    command per ligand — for a batch of N ligands that's one round-trip
    instead of 5 + 3N, removing per-call RPC/connection overhead. Caller is
    responsible for holding _pymol_lock around this.
    """
    commands = [
        "reinitialize",
        f"load {receptor_path}, receptor",
        "hide everything",
        "show cartoon, receptor",
        "color slate, receptor",
    ]
    for idx, ligand in enumerate(ligand_paths, start=1):
        obj = f"ligand_{idx}"
        commands.append(f"load {ligand}, {obj}")
        commands.append(f"show sticks, {obj}")
        commands.append(f"color yellow, {obj}")
    commands.append("bg_color white")
    commands.append("orient")
    _pymol_do("; ".join(commands))


def _set_load_status(**updates: Any) -> None:
    with _status_lock:
        _load_status.update(updates)


def _get_load_status() -> dict[str, Any]:
    with _status_lock:
        return dict(_load_status)


def _run_load_job(job_id: str, receptor_path: str, ligand_paths: list[str]) -> None:
    """Background-thread target: does the actual (potentially slow) PyMOL
    load under _pymol_lock, then records the outcome in _load_status."""
    try:
        with _pymol_lock:
            _load_into_pymol(receptor_path, ligand_paths)
    except Exception as exc:
        _set_load_status(
            status="error",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    else:
        _set_load_status(
            status="done",
            error=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _extent_from_selection(selection: str) -> tuple[list[float], list[float]]:
    # Passed as a real XML-RPC string argument to a dedicated get_extent()
    # RPC method (see docker/pymol_rpc_server.py), not spliced into a Python
    # source block -- selection can contain quotes/newlines/anything without
    # any risk of breaking out of a string literal.
    try:
        result = _rpc().get_extent(selection)
    except Exception as exc:
        raise RuntimeError(f"Could not retrieve extent from PyMOL selection: {exc}") from exc
    return result["min"], result["max"]


def _center_size_from_min_max(min_corner: list[float], max_corner: list[float]) -> tuple[list[float], list[float]]:
    center = [round((mn + mx) / 2.0, 4) for mn, mx in zip(min_corner, max_corner)]
    size = [round(abs(mx - mn), 4) for mn, mx in zip(min_corner, max_corner)]
    return center, size


def _save_box(job_id: str, center: list[float], box_size: list[float]) -> str:
    job_dir = _resolve_job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id,
        "center": center,
        "box_size": box_size,
        "source": "pymol_interactive",
    }
    out = job_dir / "docking_box.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(out)


@app.get("/health")
def health() -> Any:
    rpc_ok = True
    try:
        _rpc().ping()
    except Exception:
        rpc_ok = False
    return jsonify({"status": "ok", "service": "pymol_interactive", "pymol_rpc": rpc_ok})


@app.get("/")
def index() -> str:
    return (
        "<h2>PyMOL Interactive Service</h2>"
        "<p>Open noVNC desktop: <a href='http://localhost:6080/vnc.html' target='_blank'>http://localhost:6080/vnc.html</a></p>"
        "<p>Use API endpoints:</p>"
        "<ul>"
        "<li>POST /api/load_job</li>"
        "<li>GET /api/load_status</li>"
        "<li>POST /api/box/from_selection</li>"
        "<li>POST /api/box/from_corners</li>"
        "</ul>"
    )


@app.get("/api/jobs")
def list_jobs() -> Any:
    jobs = sorted([p.name for p in OUTPUT_ROOT.glob("job_*") if p.is_dir()], reverse=True)
    return jsonify({"jobs": jobs})


@app.post("/api/load_job")
def load_job() -> Any:
    """Kick off loading a job into the live PyMOL session in the background
    and return immediately — for a real batch, doing this inline could take
    far longer than any client's HTTP timeout. Poll GET /api/load_status for
    the outcome."""
    global _current_load_thread

    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing required field: job_id"}), 400

    try:
        job_dir = _resolve_job_dir(job_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not job_dir.exists():
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    assets = _find_job_assets(job_dir)
    receptor_path = assets.get("receptor_path")
    ligand_paths = assets.get("ligand_paths", [])

    if not receptor_path or not _exists(receptor_path):
        return jsonify({"error": "Could not find receptor file in job outputs"}), 400
    if not ligand_paths:
        return jsonify({"error": "Could not find ligand files in job outputs"}), 400

    current = _get_load_status()
    if current["status"] == "running":
        return jsonify({"status": "already_running", "job_id": current["job_id"]}), 200

    _set_load_status(
        status="running",
        job_id=job_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        error=None,
    )
    _current_load_thread = threading.Thread(
        target=_run_load_job,
        args=(job_id, receptor_path, ligand_paths),
        daemon=True,
        name=f"pymol-load-{job_id}",
    )
    _current_load_thread.start()

    return jsonify({"status": "started", "job_id": job_id}), 202


@app.get("/api/load_status")
def load_status() -> Any:
    return jsonify(_get_load_status())


@app.post("/api/box/from_selection")
def box_from_selection() -> Any:
    payload = request.get_json(silent=True) or {}
    selection = payload.get("selection", "sele")
    job_id = payload.get("job_id")

    if not job_id:
        return jsonify({"error": "Missing required field: job_id"}), 400
    if not _JOB_ID_RE.match(job_id):
        return jsonify({"error": f"Invalid job_id: {job_id!r}"}), 400

    try:
        # Held for the whole PyMOL sequence (extent lookup + marker draw), not
        # just the individual calls, so a concurrent background /api/load_job
        # can't reinitialize the session mid-selection and invalidate this.
        with _pymol_lock:
            min_corner, max_corner = _extent_from_selection(selection)
            center, box_size = _center_size_from_min_max(min_corner, max_corner)
            out_file = _save_box(job_id, center, box_size)
            # Draw/update visual box for confirmation.
            _pymol_do(
                f"pseudoatom box_center, pos=[{center[0]},{center[1]},{center[2]}];"
                "show spheres, box_center; color red, box_center"
            )
    except Exception as exc:
        return jsonify({"error": f"Failed to derive docking box from selection '{selection}': {exc}"}), 500

    return jsonify(
        {
            "status": "ok",
            "job_id": job_id,
            "selection": selection,
            "center": center,
            "box_size": box_size,
            "saved_to": out_file,
        }
    )


@app.post("/api/box/from_corners")
def box_from_corners() -> Any:
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id")
    min_corner = payload.get("min")
    max_corner = payload.get("max")

    if not job_id:
        return jsonify({"error": "Missing required field: job_id"}), 400
    if not _JOB_ID_RE.match(job_id):
        return jsonify({"error": f"Invalid job_id: {job_id!r}"}), 400
    if not isinstance(min_corner, list) or not isinstance(max_corner, list) or len(min_corner) != 3 or len(max_corner) != 3:
        return jsonify({"error": "Fields 'min' and 'max' must be 3-element arrays"}), 400

    try:
        min_corner = [float(v) for v in min_corner]
        max_corner = [float(v) for v in max_corner]
        center, box_size = _center_size_from_min_max(min_corner, max_corner)
        out_file = _save_box(job_id, center, box_size)
    except Exception as exc:
        return jsonify({"error": f"Failed to compute docking box: {exc}"}), 500

    return jsonify(
        {
            "status": "ok",
            "job_id": job_id,
            "center": center,
            "box_size": box_size,
            "saved_to": out_file,
        }
    )


if __name__ == "__main__":
    host = os.getenv("PYMOL_INTERACTIVE_HOST", "0.0.0.0")
    port = int(os.getenv("PYMOL_INTERACTIVE_PORT", "8090"))
    # waitress instead of Flask's dev server (see common/mcp_base.py for the
    # same change and rationale). A small bounded pool is enough here -- a
    # status poll should never queue behind an in-flight load/box request.
    from waitress import serve

    serve(app, host=host, port=port, threads=4)
