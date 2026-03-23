"""Interactive PyMOL helper API for noVNC sessions.

This service does not replace the MCP visualizer; it adds a browser-accessible
interactive PyMOL session and helper endpoints for loading jobs and deriving
Docking Center / Box Size from a visual selection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from xmlrpc.client import ServerProxy

from flask import Flask, jsonify, request

app = Flask(__name__)

OUTPUT_ROOT = Path(os.getenv("ORCHESTRATOR_OUTPUT_DIR", "/app/orchestrator_output")).resolve()
RPC_URL = os.getenv("PYMOL_RPC_URL", "http://127.0.0.1:9123")
EXTENT_TMP = Path("/tmp/pymol_extent.json")


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
    _pymol_do("reinitialize")
    _pymol_do(f"load {receptor_path}, receptor")
    _pymol_do("hide everything")
    _pymol_do("show cartoon, receptor")
    _pymol_do("color slate, receptor")

    for idx, ligand in enumerate(ligand_paths, start=1):
        obj = f"ligand_{idx}"
        _pymol_do(f"load {ligand}, {obj}")
        _pymol_do(f"show sticks, {obj}")
        _pymol_do(f"color yellow, {obj}")

    _pymol_do("bg_color white")
    _pymol_do("orient")


def _extent_from_selection(selection: str) -> tuple[list[float], list[float]]:
    if EXTENT_TMP.exists():
        EXTENT_TMP.unlink(missing_ok=True)

    escaped = selection.replace("'", "\\'")
    python_block = (
        "python\n"
        "import json\n"
        "from pymol import cmd\n"
        f"mn,mx=cmd.get_extent('{escaped}')\n"
        f"open('{EXTENT_TMP.as_posix()}','w').write(json.dumps({{'min':mn,'max':mx}}))\n"
        "python end"
    )
    _pymol_do(python_block)

    if not EXTENT_TMP.exists():
        raise RuntimeError("Could not retrieve extent from PyMOL selection")

    data = json.loads(EXTENT_TMP.read_text(encoding="utf-8"))
    return data["min"], data["max"]


def _center_size_from_min_max(min_corner: list[float], max_corner: list[float]) -> tuple[list[float], list[float]]:
    center = [round((mn + mx) / 2.0, 4) for mn, mx in zip(min_corner, max_corner)]
    size = [round(abs(mx - mn), 4) for mn, mx in zip(min_corner, max_corner)]
    return center, size


def _save_box(job_id: str, center: list[float], box_size: list[float]) -> str:
    job_dir = OUTPUT_ROOT / job_id
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
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing required field: job_id"}), 400

    job_dir = OUTPUT_ROOT / job_id
    if not job_dir.exists():
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    assets = _find_job_assets(job_dir)
    receptor_path = assets.get("receptor_path")
    ligand_paths = assets.get("ligand_paths", [])

    if not receptor_path or not _exists(receptor_path):
        return jsonify({"error": "Could not find receptor file in job outputs"}), 400
    if not ligand_paths:
        return jsonify({"error": "Could not find ligand files in job outputs"}), 400

    try:
        _load_into_pymol(receptor_path, ligand_paths)
    except Exception as exc:
        return jsonify({"error": f"Failed to load in PyMOL session: {exc}"}), 500

    return jsonify(
        {
            "status": "ok",
            "job_id": job_id,
            "receptor_path": receptor_path,
            "ligand_paths": ligand_paths,
        }
    )


@app.post("/api/box/from_selection")
def box_from_selection() -> Any:
    payload = request.get_json(silent=True) or {}
    selection = payload.get("selection", "sele")
    job_id = payload.get("job_id")

    if not job_id:
        return jsonify({"error": "Missing required field: job_id"}), 400

    try:
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
    app.run(host=host, port=port)
