"""Live check: Stop halts a running multi-ligand dock within seconds (Stage 13.1b).

Opt-in (``integration`` marker) -- needs the full live stack, network access
to RCSB, and a rebuilt ``mcp-moldina-runner`` image. Self-skips when
moldina-runner isn't reachable. Run from the dev script-runner, which
shares the orchestrator_output volume with the MCP services:

    docker compose --profile dev run --rm script-runner \
        python -m pytest tests/test_docking_stop_integration.py -m integration -v -s

Drives the same path the GUI's Stop button does: an OrchestratorBridge
runs the real workflow; once docking_progress.json shows a ligand in
progress, bridge.request_stop() is called and the run must end as
"cancelled" quickly, with the dock step reporting ``stopped`` -- instead of
docking every remaining ligand first. The job directory is removed
afterwards, pass or fail.
"""
import json
import os
import shutil
import time
import uuid

import pytest
import requests

from services.llm_orchestrator.orchestrator import WorkflowOrchestrator
from ui.orchestrator_bridge import OrchestratorBridge

pytestmark = pytest.mark.integration

MOLDINA_URL = os.getenv("MCP_MOLDINA_RUNNER_URL", "http://localhost:8004")

# Several cofactor-sized ligands at high exhaustiveness, docked one at a
# time, so docking is still running when Stop is pressed.
LIGANDS = ["ATP", "ADP", "GTP", "NAD", "FAD", "SAM", "COA", "HEM"]
CENTER = [-25.544, 12.854, 58.25]  # 6LU7 geometric center (Phase 12 real-data check)

# Generous: downloads + preparation happen before docking starts.
WAIT_FOR_DOCKING_S = 600
# The point of the feature: Stop must take effect in seconds, not minutes.
MAX_STOP_LATENCY_S = 20


@pytest.fixture
def live_stack():
    try:
        requests.get(f"{MOLDINA_URL}/health", timeout=5).raise_for_status()
    except requests.RequestException:
        pytest.skip(f"moldina-runner not reachable at {MOLDINA_URL}")


def _read_progress(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def test_stop_during_docking_halts_within_seconds(live_stack):
    output_base = WorkflowOrchestrator().output_base
    job_id = f"job_stoptest_{uuid.uuid4().hex[:8]}"
    job_dir = output_base / job_id
    progress_file = job_dir / "docking_results" / "docking_progress.json"
    workflow = {
        "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        "parameters": {
            "protein_id": "6lu7",
            "ligand_ids": LIGANDS,
            "center": CENTER,
            "box_size": [20.0, 20.0, 20.0],
            "exhaustiveness": 32,
            "formats": {"protein": "pdb", "ligand": "sdf"},
        },
    }
    bridge = OrchestratorBridge()
    try:
        bridge.start_workflow(workflow, job_id, str(output_base))

        deadline = time.monotonic() + WAIT_FOR_DOCKING_S
        while time.monotonic() < deadline and bridge.is_running():
            progress = _read_progress(progress_file)
            if progress and progress.get("in_progress"):
                break
            time.sleep(1)
        else:
            pytest.fail(f"docking never started within {WAIT_FOR_DOCKING_S}s: {bridge.get_error() or bridge.get_result()}")

        print(f"\nDocking in progress: {progress}")
        stop_at = time.monotonic()
        bridge.request_stop()
        bridge._thread.join(timeout=120)
        latency = time.monotonic() - stop_at
        print(f"Stopped after {latency:.1f}s")

        assert not bridge.is_running()
        assert bridge.get_error() is None
        result = bridge.get_result()
        dock = result["results"]["dock"]
        print(f"Dock result: status={dock['status']} successful={dock['successful']} "
              f"failed={dock['failed']} skipped={dock['skipped']} total={dock['total']}")
        assert result["status"] == "cancelled"
        assert dock["stopped"] is True
        assert dock["skipped"] >= 1
        assert "visualize" not in result["results"]
        assert _read_progress(progress_file)["status"] == "stopped"
        assert latency < MAX_STOP_LATENCY_S
    finally:
        if bridge.is_running():
            bridge.request_stop()
            bridge._thread.join(timeout=120)
        shutil.rmtree(job_dir, ignore_errors=True)
