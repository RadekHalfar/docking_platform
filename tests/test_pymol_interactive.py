"""Tests for interactive PyMOL helper API."""

from pathlib import Path

import pytest

from services.pymol_interactive import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with isolated output root."""
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _make_job(tmp_path: Path, job_id: str = "job_100") -> Path:
    job_dir = tmp_path / job_id
    prepared = job_dir / "prepared"
    docking = job_dir / "docking_results"
    prepared.mkdir(parents=True, exist_ok=True)
    docking.mkdir(parents=True, exist_ok=True)
    return job_dir


class TestHealth:
    def test_health_reports_rpc_ok_true(self, client, monkeypatch):
        class _RpcOk:
            def ping(self):
                return True

        monkeypatch.setattr(server, "_rpc", lambda: _RpcOk())

        response = client.get("/health")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["service"] == "pymol_interactive"
        assert payload["pymol_rpc"] is True

    def test_health_reports_rpc_ok_false(self, client, monkeypatch):
        class _RpcFail:
            def ping(self):
                raise RuntimeError("rpc down")

        monkeypatch.setattr(server, "_rpc", lambda: _RpcFail())

        response = client.get("/health")

        assert response.status_code == 200
        assert response.get_json()["pymol_rpc"] is False


class TestListJobs:
    def test_list_jobs_returns_sorted_job_ids(self, client, tmp_path):
        (tmp_path / "job_2").mkdir()
        (tmp_path / "job_10").mkdir()
        (tmp_path / "not_a_job").mkdir()

        response = client.get("/api/jobs")

        assert response.status_code == 200
        assert response.get_json()["jobs"] == ["job_2", "job_10"]


class TestLoadJob:
    def test_load_job_requires_job_id(self, client):
        response = client.post("/api/load_job", json={})

        assert response.status_code == 400
        assert "Missing required field: job_id" in response.get_json()["error"]

    def test_load_job_returns_404_for_missing_job(self, client):
        response = client.post("/api/load_job", json={"job_id": "job_missing"})

        assert response.status_code == 404
        assert "Job not found" in response.get_json()["error"]

    def test_load_job_requires_receptor(self, client, tmp_path):
        _make_job(tmp_path, "job_200")

        response = client.post("/api/load_job", json={"job_id": "job_200"})

        assert response.status_code == 400
        assert "Could not find receptor file" in response.get_json()["error"]

    def test_load_job_success(self, client, tmp_path, monkeypatch):
        job_dir = _make_job(tmp_path, "job_300")
        receptor = job_dir / "prepared" / "6LU7.pdbqt"
        ligand = job_dir / "docking_results" / "docking_output.pdbqt"
        receptor.write_text("RECEPTOR", encoding="utf-8")
        ligand.write_text("LIGAND", encoding="utf-8")

        called = {"value": False}

        def _fake_load(receptor_path, ligand_paths):
            called["value"] = True
            assert receptor_path == str(receptor)
            assert ligand_paths == [str(ligand)]

        monkeypatch.setattr(server, "_load_into_pymol", _fake_load)

        response = client.post("/api/load_job", json={"job_id": "job_300"})

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["job_id"] == "job_300"
        assert called["value"] is True

    def test_load_job_handles_pymol_error(self, client, tmp_path, monkeypatch):
        job_dir = _make_job(tmp_path, "job_301")
        (job_dir / "prepared" / "6LU7.pdbqt").write_text("RECEPTOR", encoding="utf-8")
        (job_dir / "docking_results" / "docking_output.pdbqt").write_text("LIGAND", encoding="utf-8")

        monkeypatch.setattr(server, "_load_into_pymol", lambda *_: (_ for _ in ()).throw(RuntimeError("rpc refused")))

        response = client.post("/api/load_job", json={"job_id": "job_301"})

        assert response.status_code == 500
        assert "Failed to load in PyMOL session" in response.get_json()["error"]


class TestDockingBox:
    def test_box_from_corners_requires_job_id(self, client):
        response = client.post("/api/box/from_corners", json={"min": [0, 0, 0], "max": [1, 1, 1]})

        assert response.status_code == 400
        assert "Missing required field: job_id" in response.get_json()["error"]

    def test_box_from_corners_validates_shapes(self, client):
        response = client.post("/api/box/from_corners", json={"job_id": "job_400", "min": [0, 0], "max": [1, 1, 1]})

        assert response.status_code == 400
        assert "must be 3-element arrays" in response.get_json()["error"]

    def test_box_from_corners_success_and_file_saved(self, client, tmp_path):
        response = client.post(
            "/api/box/from_corners",
            json={"job_id": "job_401", "min": [0, 2, 4], "max": [10, 12, 14]},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["center"] == [5.0, 7.0, 9.0]
        assert payload["box_size"] == [10.0, 10.0, 10.0]

        out_file = tmp_path / "job_401" / "docking_box.json"
        assert out_file.exists()

    def test_box_from_selection_success(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "_extent_from_selection", lambda _sel: ([0.0, 2.0, 4.0], [10.0, 12.0, 14.0]))
        monkeypatch.setattr(server, "_pymol_do", lambda _cmd: None)

        response = client.post(
            "/api/box/from_selection",
            json={"job_id": "job_500", "selection": "ligand_1"},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["selection"] == "ligand_1"
        assert payload["center"] == [5.0, 7.0, 9.0]
        assert payload["box_size"] == [10.0, 10.0, 10.0]

        out_file = tmp_path / "job_500" / "docking_box.json"
        assert out_file.exists()

    def test_box_from_selection_requires_job_id(self, client):
        response = client.post("/api/box/from_selection", json={"selection": "sele"})

        assert response.status_code == 400
        assert "Missing required field: job_id" in response.get_json()["error"]
