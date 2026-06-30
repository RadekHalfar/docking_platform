"""Tests for services/llm_orchestrator/job_comparator.py."""
import json

import pytest

from services.llm_orchestrator.job_comparator import compare_jobs, load_job_affinities


def _write_workflow_results(job_dir, dock_results):
    job_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "job_id": job_dir.name,
        "status": "completed",
        "results": {
            "dock": {
                "results": dock_results,
            }
        },
    }
    (job_dir / "workflow_results.json").write_text(json.dumps(data))


class TestLoadJobAffinities:
    def test_load_job_affinities_reads_results_json(self, tmp_path):
        job_dir = tmp_path / "job_abc"
        _write_workflow_results(
            job_dir,
            [
                {"ligand_id": "ATP", "best_affinity": -8.4},
                {"ligand_id": "HEM", "best_affinity": -7.1},
            ],
        )

        affinities = load_job_affinities(str(job_dir))

        assert affinities == {"ATP": -8.4, "HEM": -7.1}

    def test_load_job_affinities_missing_file_raises(self, tmp_path):
        job_dir = tmp_path / "no_such_job"

        with pytest.raises(FileNotFoundError):
            load_job_affinities(str(job_dir))

    def test_load_job_affinities_disambiguates_duplicate_ligand_ids(self, tmp_path):
        """Docking the same ligand_id twice in one job (e.g. 'two WWO ligands') must
        keep both results, not silently collapse to the last one."""
        job_dir = tmp_path / "job_dup"
        _write_workflow_results(
            job_dir,
            [
                {"ligand_id": "WWO", "best_affinity": -8.1},
                {"ligand_id": "WWO", "best_affinity": -7.9},
                {"ligand_id": "WWO", "best_affinity": -6.5},
            ],
        )

        affinities = load_job_affinities(str(job_dir))

        assert affinities == {"WWO": -8.1, "WWO #2": -7.9, "WWO #3": -6.5}

    def test_load_job_affinities_no_dock_results(self, tmp_path):
        job_dir = tmp_path / "job_no_dock"
        job_dir.mkdir(parents=True)
        (job_dir / "workflow_results.json").write_text(json.dumps({"results": {}}))

        affinities = load_job_affinities(str(job_dir))

        assert affinities == {}


class TestCompareJobs:
    def test_compare_two_jobs_all_ligands_present(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ATP", "best_affinity": -7.9}])

        comparison = compare_jobs([str(job_a), str(job_b)])

        assert comparison["ligands"] == ["ATP"]
        assert comparison["jobs"] == ["job_abc", "job_def"]
        assert comparison["affinities"] == [[-8.4, -7.9]]

    def test_compare_jobs_with_missing_ligand(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "HEM", "best_affinity": -7.1}])

        comparison = compare_jobs([str(job_a), str(job_b)])

        assert comparison["ligands"] == ["ATP", "HEM"]
        assert comparison["affinities"] == [[-8.4, None], [None, -7.1]]

    def test_compare_jobs_identifies_best_per_ligand(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "HEM", "best_affinity": -7.1}])
        _write_workflow_results(job_b, [{"ligand_id": "HEM", "best_affinity": -8.2}])

        comparison = compare_jobs([str(job_a), str(job_b)])

        assert comparison["best_per_ligand"]["HEM"] == ("job_def", -8.2)

    def test_compare_jobs_aligns_duplicate_ligand_only_present_in_one_job(self, tmp_path):
        """job_a docks WWO twice, job_b docks WWO once: 'WWO' aligns across both,
        'WWO #2' only has a value for job_a and is N/A (None) for job_b."""
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(
            job_a,
            [
                {"ligand_id": "WWO", "best_affinity": -8.1},
                {"ligand_id": "WWO", "best_affinity": -7.9},
            ],
        )
        _write_workflow_results(job_b, [{"ligand_id": "WWO", "best_affinity": -8.5}])

        comparison = compare_jobs([str(job_a), str(job_b)])

        assert comparison["ligands"] == ["WWO", "WWO #2"]
        assert comparison["affinities"] == [[-8.1, -8.5], [-7.9, None]]

    def test_compare_single_job(self, tmp_path):
        job_a = tmp_path / "job_abc"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])

        comparison = compare_jobs([str(job_a)])

        assert comparison["jobs"] == ["job_abc"]
        assert comparison["ligands"] == ["ATP"]
        assert comparison["affinities"] == [[-8.4]]

    def test_compare_jobs_empty_list_raises(self, tmp_path):
        with pytest.raises(ValueError):
            compare_jobs([])
