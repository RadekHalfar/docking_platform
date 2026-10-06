"""Tests for the Phase 3 job-comparison table helper (ui/results.py)."""
import pytest

pd = pytest.importorskip("pandas")

from services.llm_orchestrator.job_comparator import compare_jobs
from ui.results import build_comparison_dataframe, build_comparison_html


def _write_workflow_results(job_dir, dock_results):
    import json

    job_dir.mkdir(parents=True, exist_ok=True)
    data = {"status": "completed", "results": {"dock": {"results": dock_results}}}
    (job_dir / "workflow_results.json").write_text(json.dumps(data))


class TestBuildComparisonDataframe:
    def test_dataframe_shape_matches_ligands_and_jobs(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(
            job_b,
            [
                {"ligand_id": "HEM", "best_affinity": -8.2},
                {"ligand_id": "ATP", "best_affinity": -7.9},
            ],
        )

        comparison = compare_jobs([str(job_a), str(job_b)])
        df = build_comparison_dataframe(comparison)

        assert isinstance(df, pd.DataFrame)
        assert list(df.index) == ["ATP", "HEM"]
        assert list(df.columns) == ["job_abc", "job_def"]
        assert df.shape == (2, 2)

    def test_missing_ligand_shows_as_nan(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ADP", "best_affinity": -6.5}])

        comparison = compare_jobs([str(job_a), str(job_b)])
        df = build_comparison_dataframe(comparison)

        assert df.loc["ATP", "job_def"] is None or df.loc["ATP", "job_def"] != df.loc["ATP", "job_def"]

    def test_best_per_ligand_identifies_most_negative(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ATP", "best_affinity": -9.1}])

        comparison = compare_jobs([str(job_a), str(job_b)])

        assert comparison["best_per_ligand"]["ATP"] == ("job_def", -9.1)

    def test_single_job_single_column(self, tmp_path):
        job_a = tmp_path / "job_abc"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])

        comparison = compare_jobs([str(job_a)])
        df = build_comparison_dataframe(comparison)

        assert df.shape == (1, 1)
        assert df.iloc[0, 0] == -8.4


class TestBuildComparisonHtml:
    def test_highlights_best_job_cell_with_explicit_colors(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ATP", "best_affinity": -9.1}])

        comparison = compare_jobs([str(job_a), str(job_b)])
        out = build_comparison_html(comparison)

        # The stronger binder (-9.1, job_def) must carry an explicit, theme-independent
        # background + text color so it stays readable in both light and dark mode —
        # this is the fix for the black-on-black dark-mode bug.
        assert "background-color:#90ee90" in out
        assert "-9.10" in out
        assert "-8.40" in out
        # The weaker binder's cell must not be highlighted.
        weaker_cell = out.split("-8.40")[0].split("<td")[-1]
        assert "background-color" not in weaker_cell

    def test_missing_ligand_rendered_as_na(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ADP", "best_affinity": -6.5}])

        comparison = compare_jobs([str(job_a), str(job_b)])
        out = build_comparison_html(comparison)

        assert "N/A" in out

    def test_ligand_and_job_names_are_html_escaped(self, tmp_path):
        job_dir = tmp_path / "job_<script>"
        _write_workflow_results(job_dir, [{"ligand_id": "<b>ATP</b>", "best_affinity": -8.4}])

        comparison = compare_jobs([str(job_dir)])
        out = build_comparison_html(comparison)

        assert "<script>" not in out
        assert "&lt;script&gt;" in out
        assert "<b>ATP</b>" not in out
        assert "&lt;b&gt;ATP&lt;/b&gt;" in out

    def test_contains_job_names_as_column_headers(self, tmp_path):
        job_a = tmp_path / "job_abc"
        job_b = tmp_path / "job_def"
        _write_workflow_results(job_a, [{"ligand_id": "ATP", "best_affinity": -8.4}])
        _write_workflow_results(job_b, [{"ligand_id": "ATP", "best_affinity": -9.1}])

        comparison = compare_jobs([str(job_a), str(job_b)])
        out = build_comparison_html(comparison)

        assert "job_abc" in out
        assert "job_def" in out
        assert "<table" in out and "</table>" in out

    def test_algorithm_row_names_each_jobs_search(self):
        # Stage 15.3: one algorithm per job column, so a row under the headers.
        comparison = {
            "ligands": ["ATP"], "jobs": ["a", "b"], "algorithms": ["vina", "moldina"],
            "affinities": [[-5.8, -4.3]], "best_per_ligand": {"ATP": ("a", -5.8)},
        }

        html = build_comparison_html(comparison)

        assert ">Algorithm</th>" in html
        assert html.index("Vina (Monte Carlo)") < html.index("Moldina (PSO)")
        assert html.index(">Algorithm</th>") < html.index(">ATP</td>")

    def test_comparison_without_algorithms_has_no_algorithm_row(self):
        comparison = {
            "ligands": ["ATP"], "jobs": ["a"], "affinities": [[-5.8]], "best_per_ligand": {},
        }
        assert "Algorithm" not in build_comparison_html(comparison)
