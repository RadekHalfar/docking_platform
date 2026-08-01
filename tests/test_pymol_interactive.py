"""Tests for interactive PyMOL helper API."""

import threading
import time
from pathlib import Path

import pytest

from services.pymol_interactive import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with isolated output root and reset load state.

    _load_status/_current_load_thread are module-level globals — reset them
    for every test so a background thread or status left over from one test
    can't leak into the next.
    """
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_load_status", {
        "status": "idle",
        "job_id": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    })
    monkeypatch.setattr(server, "_current_load_thread", None)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _make_job(tmp_path: Path, job_id: str = "job_100") -> Path:
    job_dir = tmp_path / job_id
    prepared = job_dir / "prepared"
    docking = job_dir / "docking_results"
    prepared.mkdir(parents=True, exist_ok=True)
    docking.mkdir(parents=True, exist_ok=True)
    return job_dir


def _make_loadable_job(tmp_path: Path, job_id: str) -> tuple[Path, Path]:
    job_dir = _make_job(tmp_path, job_id)
    receptor = job_dir / "prepared" / "6LU7.pdbqt"
    ligand = job_dir / "docking_results" / "docking_output.pdbqt"
    receptor.write_text("RECEPTOR", encoding="utf-8")
    ligand.write_text("LIGAND", encoding="utf-8")
    return receptor, ligand


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


class TestLoadIntoPymolConsolidation:
    """The load sequence must be a single consolidated RPC call, not one
    call per command per ligand -- that's the whole fix for the timeout."""

    def test_load_into_pymol_issues_single_consolidated_command(self, monkeypatch):
        calls = []
        monkeypatch.setattr(server, "_pymol_do", lambda cmd: calls.append(cmd))

        server._load_into_pymol(
            "/data/receptor.pdbqt", ["/data/lig1.pdbqt", "/data/lig2.pdbqt"]
        )

        assert len(calls) == 1
        command = calls[0]
        for fragment in [
            "reinitialize",
            "load /data/receptor.pdbqt, receptor",
            "hide everything",
            "show cartoon, receptor",
            "color slate, receptor",
            "load /data/lig1.pdbqt, ligand_1",
            "show sticks, ligand_1",
            "color yellow, ligand_1",
            "load /data/lig2.pdbqt, ligand_2",
            "show sticks, ligand_2",
            "color yellow, ligand_2",
            "bg_color white",
            "orient",
        ]:
            assert fragment in command

        # Order matters: reinitialize first, orient last, ligands in order.
        assert command.index("reinitialize") < command.index("load /data/receptor.pdbqt, receptor")
        assert command.index("load /data/lig1.pdbqt, ligand_1") < command.index("load /data/lig2.pdbqt, ligand_2")
        assert command.rindex("orient") > command.index("load /data/lig2.pdbqt, ligand_2")

    def test_load_into_pymol_with_no_ligands_still_single_call(self, monkeypatch):
        calls = []
        monkeypatch.setattr(server, "_pymol_do", lambda cmd: calls.append(cmd))

        server._load_into_pymol("/data/receptor.pdbqt", [])

        assert len(calls) == 1
        assert "load /data/receptor.pdbqt, receptor" in calls[0]


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

    def test_load_job_starts_in_background_and_returns_immediately(self, client, tmp_path, monkeypatch):
        receptor, ligand = _make_loadable_job(tmp_path, "job_300")

        called = {"value": False}

        def _fake_load(receptor_path, ligand_paths):
            called["value"] = True
            assert receptor_path == str(receptor)
            assert ligand_paths == [str(ligand)]

        monkeypatch.setattr(server, "_load_into_pymol", _fake_load)

        response = client.post("/api/load_job", json={"job_id": "job_300"})

        # Kickoff returns before the load necessarily finishes.
        assert response.status_code == 202
        payload = response.get_json()
        assert payload["status"] == "started"
        assert payload["job_id"] == "job_300"

        server._current_load_thread.join(timeout=5)
        assert called["value"] is True

        status_payload = client.get("/api/load_status").get_json()
        assert status_payload["status"] == "done"
        assert status_payload["job_id"] == "job_300"
        assert status_payload["error"] is None

    def test_load_job_error_surfaces_via_status_not_kickoff_response(self, client, tmp_path, monkeypatch):
        _make_loadable_job(tmp_path, "job_301")

        monkeypatch.setattr(
            server, "_load_into_pymol",
            lambda *_: (_ for _ in ()).throw(RuntimeError("rpc refused")),
        )

        response = client.post("/api/load_job", json={"job_id": "job_301"})

        # The kickoff itself still succeeds -- spawning the thread didn't fail.
        assert response.status_code == 202

        server._current_load_thread.join(timeout=5)

        status_payload = client.get("/api/load_status").get_json()
        assert status_payload["status"] == "error"
        assert "rpc refused" in status_payload["error"]
        assert status_payload["job_id"] == "job_301"

    def test_load_status_starts_idle(self, client):
        payload = client.get("/api/load_status").get_json()
        assert payload["status"] == "idle"
        assert payload["job_id"] is None


class TestLoadJobConcurrency:
    """Only one load may run against the single live PyMOL session at a time."""

    def test_second_load_while_running_is_rejected_not_double_spawned(self, client, tmp_path, monkeypatch):
        _make_loadable_job(tmp_path, "job_600")

        release = threading.Event()
        started = threading.Event()

        def _blocking_load(receptor_path, ligand_paths):
            started.set()
            assert release.wait(timeout=5), "test setup error: release was never set"

        monkeypatch.setattr(server, "_load_into_pymol", _blocking_load)

        first = client.post("/api/load_job", json={"job_id": "job_600"})
        assert first.status_code == 202
        assert started.wait(timeout=5)
        first_thread = server._current_load_thread

        second = client.post("/api/load_job", json={"job_id": "job_600"})
        assert second.status_code == 200
        assert second.get_json() == {"status": "already_running", "job_id": "job_600"}
        # No second thread was spawned -- still the same thread object as the first call.
        assert server._current_load_thread is first_thread

        release.set()
        first_thread.join(timeout=5)

        status_payload = client.get("/api/load_status").get_json()
        assert status_payload["status"] == "done"

    def test_box_from_selection_waits_for_in_flight_load(self, client, tmp_path, monkeypatch):
        """A box-selection request arriving while a load is in progress must
        not interleave PyMOL commands with the load -- it has to wait for
        the same lock, not run concurrently against the live session."""
        _make_loadable_job(tmp_path, "job_700")

        release = threading.Event()
        started = threading.Event()
        order: list[str] = []

        def _blocking_load(receptor_path, ligand_paths):
            started.set()
            assert release.wait(timeout=5), "test setup error: release was never set"
            order.append("load_finished")

        def _fake_extent(_selection):
            order.append("selection_ran")
            return [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]

        monkeypatch.setattr(server, "_load_into_pymol", _blocking_load)
        monkeypatch.setattr(server, "_extent_from_selection", _fake_extent)
        monkeypatch.setattr(server, "_pymol_do", lambda _cmd: None)

        client.post("/api/load_job", json={"job_id": "job_700"})
        assert started.wait(timeout=5)
        load_thread = server._current_load_thread

        result: dict = {}

        def _run_box_selection():
            result["response"] = client.post(
                "/api/box/from_selection", json={"job_id": "job_700", "selection": "sele"}
            )

        box_thread = threading.Thread(target=_run_box_selection)
        box_thread.start()
        # Give the box-selection thread a realistic chance to reach (and
        # block on) the shared lock before we let the load finish. The
        # assertions below are deterministic regardless of exact timing here
        # -- box_from_selection cannot acquire the lock until release.set()
        # allows the load to release it.
        time.sleep(0.2)

        release.set()
        box_thread.join(timeout=5)
        load_thread.join(timeout=5)

        assert order == ["load_finished", "selection_ran"]
        assert result["response"].status_code == 200


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


class TestJobIdPathTraversal:
    """job_id must not be usable to escape OUTPUT_ROOT (e.g. onto arbitrary
    host paths via '..' segments or an absolute path)."""

    @pytest.mark.parametrize(
        "bad_job_id",
        ["../evil", "..", "/etc/passwd", "a/b", "a\\b", "job/../../x", ""],
    )
    def test_resolve_job_dir_rejects_traversal(self, bad_job_id):
        with pytest.raises(ValueError):
            server._resolve_job_dir(bad_job_id)

    def test_resolve_job_dir_accepts_normal_ids(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
        for good_id in ["job_20260630_131335_9bf765", "_benchmark_103c660e", "job_100"]:
            assert server._resolve_job_dir(good_id) == tmp_path / good_id

    def test_load_job_rejects_traversal_job_id(self, client):
        response = client.post("/api/load_job", json={"job_id": "../../etc"})

        assert response.status_code == 400
        assert "Invalid job_id" in response.get_json()["error"]

    def test_box_from_corners_rejects_traversal_job_id(self, client):
        response = client.post(
            "/api/box/from_corners",
            json={"job_id": "../../etc", "min": [0, 0, 0], "max": [1, 1, 1]},
        )

        assert response.status_code == 400
        assert "Invalid job_id" in response.get_json()["error"]

    def test_box_from_selection_rejects_traversal_job_id(self, client):
        response = client.post(
            "/api/box/from_selection",
            json={"job_id": "../../etc", "selection": "sele"},
        )

        assert response.status_code == 400
        assert "Invalid job_id" in response.get_json()["error"]


class TestExtentFromSelectionRpc:
    """selection must be passed as a real RPC argument, never spliced into a
    Python source string executed via cmd.do() -- that was the RCE vector."""

    def test_extent_from_selection_calls_get_extent_rpc_with_raw_selection(self, monkeypatch):
        calls = []

        class _FakeRpc:
            def get_extent(self, selection):
                calls.append(selection)
                return {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}

        monkeypatch.setattr(server, "_rpc", lambda: _FakeRpc())

        # A payload that would have broken out of the old string-literal
        # splice (quote + newline + Python statement).
        malicious = "x')\nimport os; os.system('id')\n#"
        min_corner, max_corner = server._extent_from_selection(malicious)

        assert calls == [malicious]
        assert min_corner == [0.0, 0.0, 0.0]
        assert max_corner == [1.0, 1.0, 1.0]

    def test_extent_from_selection_wraps_rpc_failure(self, monkeypatch):
        class _FailingRpc:
            def get_extent(self, selection):
                raise RuntimeError("invalid selection")

        monkeypatch.setattr(server, "_rpc", lambda: _FailingRpc())

        with pytest.raises(RuntimeError, match="Could not retrieve extent"):
            server._extent_from_selection("bogus")
