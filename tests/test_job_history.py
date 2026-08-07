"""Tests for ui/job_history.py."""
import json

import pytest

from ui.job_history import escape_markdown, list_completed_jobs, rename_job


def _write_workflow_results(
    job_dir, *, status="completed", protein_path=None, dock_results=None, job_name=None
):
    job_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "results": {
            "get_input_files": {"protein_path": protein_path} if protein_path else {},
            "dock": {"results": dock_results or []},
        },
    }
    if job_name is not None:
        data["job_name"] = job_name
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

    def test_named_job_reports_job_name(self, tmp_path):
        _write_workflow_results(tmp_path / "job_named", job_name="Overnight batch")

        jobs = list_completed_jobs(str(tmp_path))

        assert jobs[0]["job_name"] == "Overnight batch"

    def test_missing_job_name_key_defaults_to_empty_string(self, tmp_path):
        _write_workflow_results(tmp_path / "job_unnamed")

        jobs = list_completed_jobs(str(tmp_path))

        assert jobs[0]["job_name"] == ""

    def test_explicit_empty_job_name_stays_empty_string(self, tmp_path):
        _write_workflow_results(tmp_path / "job_blank_name", job_name="")

        jobs = list_completed_jobs(str(tmp_path))

        assert jobs[0]["job_name"] == ""


class TestRenameJob:
    def test_renames_job_in_place(self, tmp_path):
        job_dir = tmp_path / "job_a"
        _write_workflow_results(job_dir, job_name="Old name")

        rename_job(str(job_dir), "New name")

        data = json.loads((job_dir / "workflow_results.json").read_text())
        assert data["job_name"] == "New name"

    def test_preserves_other_keys(self, tmp_path):
        job_dir = tmp_path / "job_a"
        _write_workflow_results(
            job_dir,
            protein_path="/app/orchestrator_output/job_a/downloads/6LU7.pdb",
            dock_results=[{"ligand_id": "ATP", "best_affinity": -8.4}],
        )

        rename_job(str(job_dir), "Renamed")

        data = json.loads((job_dir / "workflow_results.json").read_text())
        assert data["status"] == "completed"
        assert data["results"]["dock"]["results"] == [{"ligand_id": "ATP", "best_affinity": -8.4}]

    def test_truncates_to_60_chars(self, tmp_path):
        job_dir = tmp_path / "job_a"
        _write_workflow_results(job_dir)

        rename_job(str(job_dir), "x" * 100)

        data = json.loads((job_dir / "workflow_results.json").read_text())
        assert data["job_name"] == "x" * 60

    def test_strips_whitespace(self, tmp_path):
        job_dir = tmp_path / "job_a"
        _write_workflow_results(job_dir)

        rename_job(str(job_dir), "  padded  ")

        data = json.loads((job_dir / "workflow_results.json").read_text())
        assert data["job_name"] == "padded"

    def test_raises_on_nonexistent_job_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rename_job(str(tmp_path / "does_not_exist"), "New name")


class TestEscapeMarkdown:
    def test_plain_text_unchanged(self):
        assert escape_markdown("Overnight batch") == "Overnight batch"

    def test_escapes_bold_asterisks(self):
        assert escape_markdown("**bold**") == "\\*\\*bold\\*\\*"

    def test_escapes_underscores(self):
        assert escape_markdown("_italic_run") == "\\_italic\\_run"

    def test_escapes_backticks(self):
        assert escape_markdown("`code`") == "\\`code\\`"

    def test_escapes_brackets_and_hash(self):
        assert escape_markdown("[link] #tag") == "\\[link\\] \\#tag"

    def test_escapes_backslash(self):
        assert escape_markdown("a\\b") == "a\\\\b"

    def test_emoji_and_unicode_pass_through(self):
        assert escape_markdown("Run 🧬 ligand ähnlich") == "Run 🧬 ligand ähnlich"
