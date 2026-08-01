"""Tests for the opt-in job-directory cleanup utility."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.llm_orchestrator.job_cleanup import _job_timestamp, delete_jobs, find_prunable_jobs


def _make_job(base: Path, when: datetime, suffix: str = "abc123") -> str:
    job_id = f"job_{when.strftime('%Y%m%d_%H%M%S')}_{suffix}"
    (base / job_id).mkdir(parents=True)
    return job_id


@pytest.fixture
def now() -> datetime:
    # Naive local time, not UTC -- matches generate_job_id() (and therefore
    # _job_timestamp()/find_prunable_jobs()'s older_than_days comparison),
    # which both build/compare against datetime.now() with no tzinfo.
    # Real current time rather than a frozen/patched value keeps the math
    # meaningful without needing to monkeypatch datetime itself.
    return datetime.now()


class TestJobTimestamp:
    def test_returns_naive_datetime_not_utc(self):
        """generate_job_id() (common/utils.py) builds this timestamp from
        datetime.now() -- naive local time, not UTC. Regression test for a
        bug where _job_timestamp() labeled it UTC, skewing every
        older_than_days comparison by the host's UTC offset."""
        ts = _job_timestamp("job_20260615_143000_abc123")
        assert ts.tzinfo is None
        assert ts == datetime(2026, 6, 15, 14, 30, 0)

    def test_comparable_against_naive_datetime_now(self):
        """The bug this guards against only ever raised on a UTC-aware vs.
        naive comparison in some code paths and silently skewed results in
        others; this just confirms the whole module stays in one
        (naive-local) timezone frame end to end."""
        ts = _job_timestamp("job_20260101_000000_abc123")
        # Must not raise TypeError: can't compare offset-naive and
        # offset-aware datetimes.
        assert ts < datetime.now()


class TestFindPrunableJobs:
    def test_no_filters_returns_empty_list(self, tmp_path, now):
        """Calling with neither filter must never mean 'everything' -- an
        empty argument list is the safest possible default."""
        _make_job(tmp_path, now)
        assert find_prunable_jobs(str(tmp_path)) == []

    def test_missing_output_base_returns_empty_list(self, tmp_path):
        assert find_prunable_jobs(str(tmp_path / "does_not_exist"), keep_last=0) == []

    def test_keep_last_excludes_n_most_recent(self, tmp_path, now):
        oldest = _make_job(tmp_path, now - timedelta(days=3), "aaa")
        middle = _make_job(tmp_path, now - timedelta(days=2), "bbb")
        newest = _make_job(tmp_path, now - timedelta(days=1), "ccc")

        result = find_prunable_jobs(str(tmp_path), keep_last=1)

        job_ids = {j["job_id"] for j in result}
        assert job_ids == {oldest, middle}
        assert newest not in job_ids

    def test_keep_last_zero_considers_all(self, tmp_path, now):
        a = _make_job(tmp_path, now - timedelta(days=1), "aaa")
        b = _make_job(tmp_path, now - timedelta(hours=1), "bbb")

        result = find_prunable_jobs(str(tmp_path), keep_last=0)

        assert {j["job_id"] for j in result} == {a, b}

    def test_older_than_days_filters_by_age(self, tmp_path, now):
        old = _make_job(tmp_path, now - timedelta(days=40), "aaa")
        recent = _make_job(tmp_path, now - timedelta(days=1), "bbb")

        result = find_prunable_jobs(str(tmp_path), older_than_days=30)

        job_ids = {j["job_id"] for j in result}
        assert old in job_ids
        assert recent not in job_ids

    def test_combined_filters_are_ANDed(self, tmp_path, now):
        """A job must satisfy *every* filter passed, not just one -- each of
        the two filters excludes a different job here, proving both are
        actually applied rather than either one alone."""
        newest = _make_job(tmp_path, now - timedelta(days=2), "aaa")  # excluded by keep_last
        too_recent = _make_job(tmp_path, now - timedelta(days=5), "bbb")  # survives keep_last, excluded by age
        old_enough = _make_job(tmp_path, now - timedelta(days=20), "ccc")  # passes both
        oldest = _make_job(tmp_path, now - timedelta(days=30), "ddd")  # passes both

        result = find_prunable_jobs(str(tmp_path), keep_last=1, older_than_days=10)

        job_ids = {j["job_id"] for j in result}
        assert job_ids == {old_enough, oldest}
        assert newest not in job_ids  # excluded by keep_last, even though also too recent
        assert too_recent not in job_ids  # survives keep_last (not the single newest) but too recent

    def test_non_job_directories_are_never_included(self, tmp_path, now):
        _make_job(tmp_path, now - timedelta(days=100))
        (tmp_path / "_benchmark_deadbeef").mkdir()
        (tmp_path / "uploads").mkdir()

        result = find_prunable_jobs(str(tmp_path), keep_last=0)

        assert all(j["job_id"].startswith("job_") for j in result)
        assert len(result) == 1

    def test_malformed_job_name_is_skipped(self, tmp_path, now):
        _make_job(tmp_path, now - timedelta(days=100))
        (tmp_path / "job_not_a_real_timestamp").mkdir()

        result = find_prunable_jobs(str(tmp_path), keep_last=0)

        assert len(result) == 1

    def test_result_ordering_is_newest_excluded_first(self, tmp_path, now):
        a = _make_job(tmp_path, now - timedelta(days=3), "aaa")
        b = _make_job(tmp_path, now - timedelta(days=2), "bbb")
        c = _make_job(tmp_path, now - timedelta(days=1), "ccc")

        result = find_prunable_jobs(str(tmp_path), keep_last=0)

        assert [j["job_id"] for j in result] == [c, b, a]


class TestDeleteJobs:
    def test_deletes_existing_directories(self, tmp_path, now):
        job_id = _make_job(tmp_path, now)
        job_dir = tmp_path / job_id
        (job_dir / "workflow_results.json").write_text("{}")

        deleted, errors = delete_jobs([str(job_dir)])

        assert deleted == 1
        assert errors == []
        assert not job_dir.exists()

    def test_missing_directory_is_reported_not_raised(self, tmp_path):
        missing = tmp_path / "job_missing"

        deleted, errors = delete_jobs([str(missing)])

        assert deleted == 0
        assert len(errors) == 1
        assert "job_missing" in errors[0]

    def test_one_failure_does_not_stop_the_rest(self, tmp_path, now):
        good = tmp_path / _make_job(tmp_path, now, "aaa")
        missing = tmp_path / "job_missing"

        deleted, errors = delete_jobs([str(missing), str(good)])

        assert deleted == 1
        assert len(errors) == 1
        assert not good.exists()
