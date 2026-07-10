"""Tests for ui/job_history.py."""
import json

from ui.job_history import list_completed_jobs


def _write_workflow_results(job_dir, *, status="completed", protein_path=None, dock_results=None):
    job_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "results": {
            "get_input_files": {"protein_path": protein_path} if protein_path else {},
            "dock": {"results": dock_results or []},
        },
    }
    (job_dir / "workflow_results.json").write_text(json.dumps(data))


class TestListCompletedJobs:
    def test_finds_results_files(self, tmp_path):
        _write_workflow_results(
            tmp_path / "job_a",
            protein_path="/app/orchestrator_output/job_a/downloads/6LU7.pdb",
            dock_results=[{"ligand_id": "ATP", "best_affinity": -8.4}],
        )
        _write_workflow_results(
            tmp_path / "job_b",
            protein_path="/app/orchestrator_output/job_b/downloads/2XYZ.pdb",
            dock_results=[
                {"ligand_id": "HEM", "best_affinity": -7.1},
                {"ligand_id": "ADP", "best_affinity": -6.5},
            ],
        )

        jobs = list_completed_jobs(str(tmp_path))

        assert len(jobs) == 2
        job_ids = {j["job_id"] for j in jobs}
        assert job_ids == {"job_a", "job_b"}
        by_id = {j["job_id"]: j for j in jobs}
        assert by_id["job_a"]["protein_id"] == "6LU7"
        assert by_id["job_a"]["ligand_count"] == 1
        assert by_id["job_b"]["ligand_count"] == 2

    def test_excludes_failed_jobs(self, tmp_path):
        _write_workflow_results(tmp_path / "job_ok", status="completed")
        _write_workflow_results(tmp_path / "job_bad", status="failed")

        jobs = list_completed_jobs(str(tmp_path))

        assert [j["job_id"] for j in jobs] == ["job_ok"]

    def test_sorts_newest_first(self, tmp_path):
        _write_workflow_results(tmp_path / "job_20260101_000000_aaa")
        _write_workflow_results(tmp_path / "job_20260201_000000_bbb")

        jobs = list_completed_jobs(str(tmp_path))

        assert [j["job_id"] for j in jobs] == [
            "job_20260201_000000_bbb",
            "job_20260101_000000_aaa",
        ]

    def test_empty_dir_returns_empty_list(self, tmp_path):
        assert list_completed_jobs(str(tmp_path)) == []

    def test_nonexistent_dir_returns_empty_list(self, tmp_path):
        assert list_completed_jobs(str(tmp_path / "does_not_exist")) == []

    def test_missing_protein_path_defaults_to_placeholder(self, tmp_path):
        _write_workflow_results(tmp_path / "job_no_protein")

        jobs = list_completed_jobs(str(tmp_path))

        assert jobs[0]["protein_id"] == "?"

    def test_corrupt_results_file_is_skipped(self, tmp_path):
        job_dir = tmp_path / "job_corrupt"
        job_dir.mkdir(parents=True)
        (job_dir / "workflow_results.json").write_text("{not valid json")

        assert list_completed_jobs(str(tmp_path)) == []
