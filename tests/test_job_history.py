"""Tests for ui/job_history.py."""
import json

import pytest

from ui import job_history
from ui.job_history import (
    escape_markdown,
    list_completed_and_failed_jobs,
    rename_job,
)


def list_completed_jobs(output_base):
    return list_completed_and_failed_jobs(output_base)[0]


def list_failed_jobs(output_base):
    return list_completed_and_failed_jobs(output_base)[1]


def _write_workflow_results(
    job_dir, *, status="completed", protein_path=None, dock_results=None, job_name=None,
    errors=None,
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
    if errors is not None:
        data["errors"] = errors
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


class TestListFailedJobs:
    def test_finds_failed_jobs(self, tmp_path):
        _write_workflow_results(
            tmp_path / "job_bad",
            status="failed",
            protein_path="/app/orchestrator_output/job_bad/downloads/6LU7.pdb",
            errors=["MCP call to protein-preparer failed"],
        )

        jobs = list_failed_jobs(str(tmp_path))

        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_bad"
        assert jobs[0]["protein_id"] == "6LU7"
        assert jobs[0]["first_error"] == "MCP call to protein-preparer failed"

    def test_finds_cancelled_jobs(self, tmp_path):
        _write_workflow_results(
            tmp_path / "job_stopped",
            status="cancelled",
            errors=["Workflow cancelled by user request"],
        )

        jobs = list_failed_jobs(str(tmp_path))

        assert [j["job_id"] for j in jobs] == ["job_stopped"]
        assert jobs[0]["first_error"] == "Workflow cancelled by user request"

    def test_excludes_completed_jobs(self, tmp_path):
        _write_workflow_results(tmp_path / "job_ok", status="completed")
        _write_workflow_results(tmp_path / "job_bad", status="failed", errors=["boom"])

        jobs = list_failed_jobs(str(tmp_path))

        assert [j["job_id"] for j in jobs] == ["job_bad"]

    def test_missing_errors_defaults_to_unknown(self, tmp_path):
        _write_workflow_results(tmp_path / "job_bad", status="failed")

        jobs = list_failed_jobs(str(tmp_path))

        assert jobs[0]["first_error"] == "Unknown error"

    def test_empty_errors_list_defaults_to_unknown(self, tmp_path):
        _write_workflow_results(tmp_path / "job_bad", status="failed", errors=[])

        jobs = list_failed_jobs(str(tmp_path))

        assert jobs[0]["first_error"] == "Unknown error"

    def test_surfaces_only_first_error(self, tmp_path):
        _write_workflow_results(
            tmp_path / "job_bad", status="failed", errors=["first problem", "second problem"]
        )

        jobs = list_failed_jobs(str(tmp_path))

        assert jobs[0]["first_error"] == "first problem"

    def test_sorts_newest_first(self, tmp_path):
        _write_workflow_results(tmp_path / "job_20260101_000000_aaa", status="failed", errors=["x"])
        _write_workflow_results(tmp_path / "job_20260201_000000_bbb", status="failed", errors=["x"])

        jobs = list_failed_jobs(str(tmp_path))

        assert [j["job_id"] for j in jobs] == [
            "job_20260201_000000_bbb",
            "job_20260101_000000_aaa",
        ]

    def test_empty_dir_returns_empty_list(self, tmp_path):
        assert list_failed_jobs(str(tmp_path)) == []

    def test_nonexistent_dir_returns_empty_list(self, tmp_path):
        assert list_failed_jobs(str(tmp_path / "does_not_exist")) == []

    def test_no_ligand_count_field(self, tmp_path):
        """Failed jobs surface first_error, not ligand_count -- the two
        list functions' extra field is genuinely different, not just an
        alternate name for the same data."""
        _write_workflow_results(tmp_path / "job_bad", status="failed", errors=["boom"])

        jobs = list_failed_jobs(str(tmp_path))

        assert "ligand_count" not in jobs[0]


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

    def test_uses_lock_and_atomic_write_leaving_no_droppings(self, tmp_path):
        """Renaming shares the orchestrator's own .workflow_results.lock
        (services/llm_orchestrator/orchestrator.py) and atomic_write_json --
        without them, a rename racing a concurrent redock's locked
        read-merge-write to the same file could clobber its freshly-merged
        results (docs/reviews/stage_11_2_11_3_r2.md finding #3).
        """
        job_dir = tmp_path / "job_a"
        _write_workflow_results(job_dir, job_name="Old name")

        rename_job(str(job_dir), "New name")

        assert not (job_dir / ".workflow_results.lock").exists()
        assert not (job_dir / "workflow_results.json.tmp").exists()

    def test_waits_for_lock_held_by_another_writer(self, tmp_path):
        """A concurrent holder of .workflow_results.lock (e.g. the
        orchestrator's own merge-write) must make rename_job wait rather
        than race it -- proves rename_job actually acquires the same lock
        instead of writing straight past it.
        """
        import threading
        import time

        from common.file_io import FileLock

        job_dir = tmp_path / "job_a"
        _write_workflow_results(job_dir, job_name="Old name")

        lock = FileLock(job_dir / ".workflow_results.lock")
        lock.acquire()
        order: list = []

        def _release_after_delay():
            time.sleep(0.3)
            order.append("released")
            lock.release()

        threading.Thread(target=_release_after_delay).start()
        rename_job(str(job_dir), "New name")
        order.append("renamed")

        assert order == ["released", "renamed"]


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


def _count_reads(monkeypatch):
    reads = []
    real = job_history.load_json_dict
    monkeypatch.setattr(job_history, "load_json_dict", lambda path: (reads.append(path.parent.name), real(path))[1])
    return reads


class TestListCompletedAndFailedJobs:
    def test_a_relist_reads_only_the_sessions_that_changed(self, tmp_path, monkeypatch):
        # Review Stage R4: every Sessions tab open re-parsed every session
        # (4-6 s for ~190 on a bind mount).
        _write_workflow_results(tmp_path / "job_a", dock_results=[{"ligand_id": "ATP"}])
        _write_workflow_results(tmp_path / "job_b", status="failed", errors=["boom"])
        list_completed_and_failed_jobs(str(tmp_path))
        reads = _count_reads(monkeypatch)

        completed, failed = list_completed_and_failed_jobs(str(tmp_path))
        assert reads == [] and [j["job_id"] for j in completed + failed] == ["job_a", "job_b"]

        rename_job(str(tmp_path / "job_a"), "renamed and longer")
        completed, _ = list_completed_and_failed_jobs(str(tmp_path))
        assert reads == ["job_a"] and completed[0]["job_name"] == "renamed and longer"

    def test_a_listing_entry_changed_by_the_caller_does_not_change_the_next_listing(self, tmp_path):
        # The GUI writes a rename into the entry it holds (Review Stage R3).
        _write_workflow_results(tmp_path / "job_a", job_name="old")
        completed, _ = list_completed_and_failed_jobs(str(tmp_path))
        completed[0]["job_name"] = "changed"
        assert list_completed_and_failed_jobs(str(tmp_path))[0][0]["job_name"] == "old"

    def test_a_deleted_session_leaves_the_listing(self, tmp_path):
        import shutil
        _write_workflow_results(tmp_path / "job_a")
        _write_workflow_results(tmp_path / "job_b")
        list_completed_and_failed_jobs(str(tmp_path))
        shutil.rmtree(tmp_path / "job_a")
        completed, _ = list_completed_and_failed_jobs(str(tmp_path))
        assert [j["job_id"] for j in completed] == ["job_b"]

    def test_one_scan_gives_both_lists(self, tmp_path, monkeypatch):
        # Parsing every results file is the slow part (4-6 s for ~170
        # sessions on a bind mount); the GUI read them twice (Review Stage R3).
        _write_workflow_results(tmp_path / "job_a", dock_results=[{"ligand_id": "ATP"}])
        _write_workflow_results(tmp_path / "job_b", status="failed", errors=["boom"])
        _write_workflow_results(tmp_path / "job_c", status="cancelled")
        _write_workflow_results(tmp_path / "job_d", status="running")
        reads = _count_reads(monkeypatch)

        completed, failed = list_completed_and_failed_jobs(str(tmp_path))

        assert len(reads) == 4
        assert [j["job_id"] for j in completed] == ["job_a"]
        assert [(j["job_id"], j["first_error"]) for j in failed] == [
            ("job_c", "Unknown error"), ("job_b", "boom"),
        ]

    def test_a_results_file_that_is_not_an_object_is_skipped(self, tmp_path):
        # It raised AttributeError and lost the whole listing.
        _write_workflow_results(tmp_path / "job_a")
        (tmp_path / "job_b").mkdir()
        (tmp_path / "job_b" / "workflow_results.json").write_text("[1, 2]")

        completed, failed = list_completed_and_failed_jobs(str(tmp_path))

        assert [j["job_id"] for j in completed] == ["job_a"] and failed == []
