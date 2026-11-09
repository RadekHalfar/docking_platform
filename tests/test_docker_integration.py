"""
Docker Container Integration Tests

Tests that verify Docker images and services work correctly, including a
complete real docking through the running services.
Run these tests after building Docker images:
    docker-compose up -d
    pytest tests/test_docker_integration.py -v

Requirements:
- Docker images built
- Docker Compose running
- Services accessible on localhost:8001-8004
- Network access to RCSB

When running inside a container, set the SERVICE_HOST env var to reach host
services (e.g. SERVICE_HOST=host.docker.internal on Docker Desktop for Windows):
    docker compose --profile dev run --rm -e SERVICE_HOST=host.docker.internal script-runner python -m pytest tests/test_docker_integration.py --tb=short
"""
import os
import shutil
import uuid
import pytest
import requests
import time
from pathlib import Path

from common.mcp_client import call_mcp_tool

pytestmark = pytest.mark.integration

# Resolve service host: use env override or fall back to localhost
_SERVICE_HOST = os.getenv("SERVICE_HOST", "localhost")

# Repo root as seen from wherever pytest runs (tests/ is always one level down).
_REPO_ROOT = Path(__file__).resolve().parent.parent

CENTER = [-25.544, 12.854, 58.25]  # 6LU7 geometric center


@pytest.fixture
def docker_cache_subdir():
    """A container-writable path for save_dir/output_dir args, plus host-side cleanup.

    A pytest tmp_path is a HOST filesystem path; it's never a real location
    inside the (separate) container filesystem, whether or not it happens
    to also exist as a literal (backslash-containing, on Windows) directory
    name under the container's CWD. /app/cache is the one directory every
    service can actually write to regardless of running as root or the
    non-root `appuser` (see docker/base.Dockerfile) -- and since it's
    bind-mounted to ./cache on the host, results land somewhere real and
    inspectable too. Yields the container-side path; cleans up its host-side
    mirror under ./cache afterward so these tests don't leave debris in the
    real cache/ directory.
    """
    name = f"test_docker_integration_{uuid.uuid4().hex[:8]}"
    yield f"/app/cache/{name}"
    shutil.rmtree(_REPO_ROOT / "cache" / name, ignore_errors=True)

# Service endpoints
SERVICES = {
    "rcsb_downloader": f"http://{_SERVICE_HOST}:8001",
    "ligand_preparer": f"http://{_SERVICE_HOST}:8002",
    "protein_preparer": f"http://{_SERVICE_HOST}:8003",
    "moldina_runner": f"http://{_SERVICE_HOST}:8004",
}


def _host_path(container_path):
    """Where a file under the /app/cache bind mount lands in this checkout."""
    return _REPO_ROOT / container_path.removeprefix("/app/")


def _assert_written(container_path):
    assert _host_path(container_path).stat().st_size > 0, container_path


@pytest.fixture(scope="module")
def wait_for_services():
    """Wait for all services to be healthy before running tests"""
    max_wait = 60  # seconds
    start_time = time.time()

    all_healthy = False
    while time.time() - start_time < max_wait:
        all_healthy = True
        for name, url in SERVICES.items():
            try:
                response = requests.get(f"{url}/health", timeout=5)
                if response.status_code != 200:
                    all_healthy = False
                    break
            except requests.RequestException:
                all_healthy = False
                break

        if all_healthy:
            print("\n✅ All services are healthy")
            return True

        time.sleep(2)

    if not all_healthy:
        pytest.skip("Services not healthy after 60s - ensure docker-compose is running")

    return True


class TestDockerHealthChecks:
    """Test that all Docker services are running and healthy"""

    @pytest.mark.parametrize(
        "service_key",
        [
            "rcsb_downloader",
            "ligand_preparer",
            "protein_preparer",
            "moldina_runner",
        ],
    )
    def test_service_health(self, service_key, wait_for_services):
        """Test health endpoint for all services."""
        response = requests.get(f"{SERVICES[service_key]}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestDockerServiceFunctionality:
    """Test basic functionality of services in Docker"""

    def test_rcsb_download_in_docker(self, wait_for_services, docker_cache_subdir):
        """Test downloading structure through dockerized service"""
        result = call_mcp_tool(SERVICES["rcsb_downloader"], "download_structures", {
            "protein_id": "6lu7",
            "ligand_ids": [],
            "formats": {"protein": "pdb"},
            "save_dir": docker_cache_subdir,
        })

        _assert_written(result["protein_path"])

    def test_ligand_preparation_in_docker(self, wait_for_services, docker_cache_subdir):
        """Test ligand preparation through dockerized service.

        Downloads into the shared /app/cache bind mount (see
        docker_cache_subdir) so the file rcsb-downloader writes is actually
        visible to ligand-preparer too -- both containers mount the same
        host ./cache directory. A host pytest tmp_path would not be: it's
        never a real path inside either container's filesystem.
        """
        download = call_mcp_tool(SERVICES["rcsb_downloader"], "download_structures", {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP"],
            "formats": {"ligand": "sdf"},
            "save_dir": docker_cache_subdir,
        })

        prepared = call_mcp_tool(SERVICES["ligand_preparer"], "prepare_ligands", {
            "ligand_paths": download["ligand_paths"],
            "output_dir": f"{docker_cache_subdir}/prepared",
            "ph": 7.4,
        }, timeout=60)

        assert prepared["status"] == "ok"
        assert prepared["successful"] == 1
        _assert_written(prepared["prepared_ligands"][0]["pdbqt_path"])


@pytest.mark.slow
class TestDockerEndToEndWorkflow:
    """Test complete workflow through Docker services"""

    def test_complete_docking_workflow_in_docker(self, wait_for_services, docker_cache_subdir):
        """Full workflow, each step reading the last one's real output:
        download -> prepare -> dock -> convert poses. Kept small (a low PSO
        population, one ligand) to take seconds, not minutes."""
        download = call_mcp_tool(SERVICES["rcsb_downloader"], "download_structures", {
            "protein_id": "6lu7",
            "ligand_ids": ["ATP"],
            "formats": {"protein": "pdb", "ligand": "sdf"},
            "save_dir": f"{docker_cache_subdir}/downloaded",
        })

        ligands = call_mcp_tool(SERVICES["ligand_preparer"], "prepare_ligands", {
            "ligand_paths": download["ligand_paths"],
            "output_dir": f"{docker_cache_subdir}/prepared",
        }, timeout=60)
        protein = call_mcp_tool(SERVICES["protein_preparer"], "prepare_proteins", {
            "protein_paths": [download["protein_path"]],
            "output_dir": f"{docker_cache_subdir}/prepared",
        }, timeout=60)

        dock = call_mcp_tool(SERVICES["moldina_runner"], "run_docking", {
            "receptor_path": protein["prepared_proteins"][0]["pdbqt_path"],
            "ligand_paths": [ligands["prepared_ligands"][0]["pdbqt_path"]],
            "output_dir": f"{docker_cache_subdir}/docking",
            "center": CENTER,
            "box_size": [20.0, 20.0, 20.0],
            "population": 20,
            "num_modes": 3,
        })

        assert dock["status"] == "ok"
        assert dock["successful"] == 1
        result = dock["results"][0]
        assert result["ligand_id"] == "ATP"
        # No threshold on the score: the search is random, and this small
        # population scores ATP anywhere from about -2 to well above 0
        # (measured against the live stack, 2026-11-06). What must hold is
        # that the modes are ranked and best_affinity is the first of them.
        affinities = [m["affinity"] for m in result["modes"]]
        assert affinities == sorted(affinities)
        assert result["best_affinity"] == affinities[0]
        _assert_written(result["output_path"])

        poses = call_mcp_tool(SERVICES["ligand_preparer"], "convert_docked_poses", {
            "pose_results": dock["results"],
            "output_dir": f"{docker_cache_subdir}/poses",
        }, timeout=60)

        assert poses["status"] == "ok"
        _assert_written(poses["converted"][0]["sdf_path"])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
