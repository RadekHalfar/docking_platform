"""Tests for ui.worker_cap.detect_moldina_worker_cap (no GUI framework dependency)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from ui.worker_cap import (
    MEASURED_AT_CPUS,
    MEASURED_BEST_WORKERS,
    MEASURED_PEAK_MIB_PER_WORKER,
    WorkerCap,
    default_worker_count,
    describe_worker_cap,
    detect_moldina_worker_cap,
    max_workers_for,
)

_PSO_BEST = MEASURED_BEST_WORKERS["moldina"]
_PSO_MIB = MEASURED_PEAK_MIB_PER_WORKER["moldina"]


def _write_compose(tmp_path, cpus):
    compose_path = tmp_path / "docker-compose.yml"
    if cpus is None:
        body = "services:\n  moldina-runner: {}\n"
    else:
        body = (
            "services:\n"
            "  moldina-runner:\n"
            "    deploy:\n"
            "      resources:\n"
            "        limits:\n"
            f"          cpus: '{cpus}'\n"
        )
    compose_path.write_text(body)
    return compose_path


def _write_raw(tmp_path, body):
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(body)
    return compose_path


class TestDetectMoldinaWorkerCap:
    def test_reads_configured_cpu_limit(self, tmp_path):
        compose_path = _write_compose(tmp_path, "4")
        # The cap is min(quota, cpu_count); pin the CPUs so a 2-CPU CI
        # runner doesn't make it 2.
        with patch("ui.worker_cap.os.cpu_count", return_value=16):
            assert detect_moldina_worker_cap(compose_path) == WorkerCap(4, "compose")

    def test_truncates_fractional_cpu_limit(self, tmp_path):
        compose_path = _write_compose(tmp_path, "2.5")
        with patch("ui.worker_cap.os.cpu_count", return_value=16):
            assert detect_moldina_worker_cap(compose_path) == WorkerCap(2, "compose")

    def test_fractional_limit_below_one_is_one_worker(self, tmp_path):
        # The server floors the quota and never goes below 1
        # (MoldinaRunner._detect_effective_cpu_count); 0.5 is a valid limit,
        # not a reason to fall back to the whole machine.
        compose_path = _write_compose(tmp_path, "0.5")
        with patch("ui.worker_cap.os.cpu_count", return_value=16):
            assert detect_moldina_worker_cap(compose_path) == WorkerCap(1, "compose")

    def test_limit_above_machine_cores_is_clamped_to_them(self, tmp_path):
        # Same rule as the server: cpus '10' on a 4-core host can't bind.
        compose_path = _write_compose(tmp_path, "10")
        with patch("ui.worker_cap.os.cpu_count", return_value=4):
            assert detect_moldina_worker_cap(compose_path) == WorkerCap(4, "compose")

    @pytest.mark.parametrize("body", ["cpus: .inf", "cpus: .nan", "cpus: '1e999'"])
    def test_falls_back_on_infinite_or_nan_limit(self, tmp_path, body):
        compose_path = _write_raw(
            tmp_path,
            "services:\n  moldina-runner:\n    deploy:\n      resources:\n"
            f"        limits:\n          {body}\n",
        )
        with patch("ui.worker_cap.os.cpu_count", return_value=5):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (5, "fallback")
        assert "not a positive number" in result.reason

    def test_falls_back_on_non_utf8_file(self, tmp_path):
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_bytes(b"services:\n  ui: {image: \xff\xfe}\n")
        with patch("ui.worker_cap.os.cpu_count", return_value=3):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (3, "fallback")
        assert "not UTF-8" in result.reason

    def test_falls_back_when_file_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist.yml"
        with patch("ui.worker_cap.os.cpu_count", return_value=6):
            result = detect_moldina_worker_cap(missing)
        assert (result.cap, result.source) == (6, "fallback")
        assert "not found" in result.reason
        assert str(missing) in result.reason

    def test_falls_back_on_malformed_yaml(self, tmp_path):
        compose_path = _write_raw(tmp_path, "not: [valid: yaml: at: all")
        with patch("ui.worker_cap.os.cpu_count", return_value=2):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (2, "fallback")
        assert "not valid YAML" in result.reason

    def test_falls_back_on_empty_file(self, tmp_path):
        compose_path = _write_raw(tmp_path, "")
        with patch("ui.worker_cap.os.cpu_count", return_value=2):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (2, "fallback")
        assert "services.moldina-runner.deploy.resources.limits.cpus" in result.reason

    def test_falls_back_when_service_missing(self, tmp_path):
        compose_path = _write_raw(tmp_path, "services:\n  ui: {}\n")
        with patch("ui.worker_cap.os.cpu_count", return_value=4):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (4, "fallback")
        assert "services.moldina-runner.deploy.resources.limits.cpus" in result.reason

    def test_falls_back_when_no_limit_configured(self, tmp_path):
        compose_path = _write_compose(tmp_path, None)
        with patch("ui.worker_cap.os.cpu_count", return_value=3):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (3, "fallback")
        assert "limits.cpus" in result.reason

    def test_falls_back_when_deploy_is_null(self, tmp_path):
        compose_path = _write_raw(tmp_path, "services:\n  moldina-runner:\n    deploy:\n")
        with patch("ui.worker_cap.os.cpu_count", return_value=3):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (3, "fallback")
        assert "limits.cpus" in result.reason

    def test_falls_back_when_limit_is_zero(self, tmp_path):
        compose_path = _write_compose(tmp_path, "0")
        with patch("ui.worker_cap.os.cpu_count", return_value=5):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (5, "fallback")
        assert "not a positive number" in result.reason

    def test_falls_back_when_limit_is_negative(self, tmp_path):
        compose_path = _write_compose(tmp_path, "-2")
        with patch("ui.worker_cap.os.cpu_count", return_value=5):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (5, "fallback")
        assert "not a positive number" in result.reason

    def test_falls_back_when_limit_is_not_numeric(self, tmp_path):
        compose_path = _write_compose(tmp_path, "lots")
        with patch("ui.worker_cap.os.cpu_count", return_value=5):
            result = detect_moldina_worker_cap(compose_path)
        assert (result.cap, result.source) == (5, "fallback")
        assert "'lots' is not a number" in result.reason

    def test_defaults_to_1_if_cpu_count_unknown(self, tmp_path):
        missing = tmp_path / "does_not_exist.yml"
        with patch("ui.worker_cap.os.cpu_count", return_value=None):
            result = detect_moldina_worker_cap(missing)
        assert (result.cap, result.source) == (1, "fallback")
        assert "not found" in result.reason

    def test_defaults_to_repo_docker_compose_when_no_path_given(self):
        # Exercises the real default-path branch against this repo's actual
        # docker-compose.yml, which does configure moldina-runner's limit.
        result = detect_moldina_worker_cap()
        assert result.source == "compose"
        assert result.reason is None
        assert result.cap >= 1


class TestDescribeWorkerCap:
    def test_compose_source_has_caption_and_no_warning(self):
        warning, caption = describe_worker_cap(WorkerCap(MEASURED_AT_CPUS, "compose"))
        assert warning is None
        assert caption.startswith(
            f"Docking service is limited to {MEASURED_AT_CPUS} CPUs (docker-compose.yml)."
        )
        assert "memory usage" in caption
        assert _measured_text("moldina", "Moldina (PSO)") in caption
        assert "may not hold here" not in caption

    def test_caption_is_the_selected_algorithms(self):
        _, caption = describe_worker_cap(WorkerCap(MEASURED_AT_CPUS, "compose"), "vina")
        assert _measured_text("vina", "Vina (Monte Carlo)") in caption
        assert "Moldina" not in caption

    @pytest.mark.parametrize("cap, cpus_text", [(1, "1 CPU "), (4, "4 CPUs"), (MEASURED_AT_CPUS + 6, "16 CPUs")])
    def test_compose_cap_other_than_benchmarked_names_the_conditions(self, cap, cpus_text):
        # At cpus '4' the widget's max is 4 while the benchmark said 8; the
        # caption must not present 10-CPU numbers as if they applied here.
        _, caption = describe_worker_cap(WorkerCap(cap, "compose"))
        assert f"limited to {cpus_text}" in caption
        assert (f"Benchmarked best for Moldina (PSO) at a {MEASURED_AT_CPUS}-CPU limit, "
                f"which may not hold here: {_PSO_BEST} workers") in caption

    def test_fallback_source_warns_with_reason(self):
        warning, caption = describe_worker_cap(WorkerCap(16, "fallback", "some reason"))
        assert "Couldn't read the docking service's CPU limit" in warning
        assert "The real limit may be lower." in warning
        assert "(some reason)" in warning
        assert "memory usage" in caption
        assert f"{_PSO_BEST} workers at 1 CPU each (sampled peak ~{_PSO_MIB}" in caption
        assert "may not hold here" in caption


def _measured_text(algorithm: str, label: str) -> str:
    return (
        f"Benchmarked best for {label}: {MEASURED_BEST_WORKERS[algorithm]} workers at 1 CPU each "
        f"(sampled peak ~{MEASURED_PEAK_MIB_PER_WORKER[algorithm]} MiB memory each"
    )


class TestMaxWorkersFor:
    @pytest.mark.parametrize("cap, cpu, expected", [
        (10, 1, 10), (10, 2, 5), (10, 3, 3), (10, 4, 2), (10, 10, 1), (10, 16, 1), (10, 0, 10),
    ])
    def test_is_cap_divided_by_cpu_never_below_one(self, cap, cpu, expected):
        # Same rule as moldina-runner's clamp: workers <= quota // cpu.
        assert max_workers_for(WorkerCap(cap, "compose"), cpu) == expected


class TestDefaultWorkerCount:
    @pytest.mark.parametrize("cap", [1, _PSO_BEST - 1, _PSO_BEST, _PSO_BEST + 1, 64])
    def test_is_measured_best_clamped_to_cap(self, cap):
        assert default_worker_count(WorkerCap(cap, "compose")) == min(_PSO_BEST, cap)

    def test_fallback_cap_is_clamped_too(self):
        assert default_worker_count(WorkerCap(2, "fallback", "r")) == 2

    @pytest.mark.parametrize("algorithm", ["moldina", "vina"])
    def test_is_per_algorithm(self, algorithm):
        assert default_worker_count(WorkerCap(64, "compose"), algorithm) == MEASURED_BEST_WORKERS[algorithm]

    @pytest.mark.parametrize("cpu", [1, 2, 4, 8])
    def test_never_above_cap_divided_by_cpu(self, cpu):
        cap = WorkerCap(10, "compose")
        assert default_worker_count(cap, "vina", cpu) == min(MEASURED_BEST_WORKERS["vina"], 10 // cpu)


_BENCHMARKS_DIR = Path(__file__).resolve().parents[1] / "docs" / "benchmarks"


class TestMeasuredConstantsMatchBenchmark:
    """The MEASURED_* constants are copied from checked-in benchmark runs;
    re-derive them from those JSONs with the documented rule so the two
    can't drift apart."""

    # Per algorithm, at 1 CPU per ligand: moldina's run and its same-day
    # re-run with the wall-clock bias fixed (both must agree), and vina's
    # run (docs/BATCH_DOCKING.md, "Benchmark baseline").
    BENCHMARKS = [
        ("moldina", "2026-10-05_moldina_cpus10_mem8g.json"),
        ("moldina", "2026-10-05_moldina_cpus10_mem8g_r1_rerun.json"),
        ("vina", "2026-10-06_vina_cpus10_mem8g.json"),
    ]

    @pytest.fixture(params=BENCHMARKS, ids=lambda b: b[1].removesuffix(".json"))
    def benchmark(self, request):
        algorithm, name = request.param
        return algorithm, _BENCHMARKS_DIR / name

    @pytest.fixture
    def data(self, benchmark):
        data = json.loads(benchmark[1].read_text(encoding="utf-8"))
        assert all("error" not in r and r["failed"] == 0 for r in data["results"])
        return data

    def test_benchmark_is_for_its_algorithm_at_one_cpu(self, benchmark, data):
        # The 14.3 JSONs predate both keys; they were PSO at cpu 1.
        assert data.get("docking_algorithm", "moldina") == benchmark[0]
        assert data.get("cpu", 1) == 1

    def test_best_workers_is_smallest_within_5_percent_of_fastest(self, benchmark, data):
        rows = data["results"]
        fastest = min(r["elapsed_seconds"] for r in rows)
        within = [r["workers"] for r in rows if r["elapsed_seconds"] <= fastest * 1.05]
        assert MEASURED_BEST_WORKERS[benchmark[0]] == min(within)

    def test_peak_per_worker_is_worst_sampled_value_rounded_up(self, benchmark, data):
        worst = max(r["stats"]["peak_mem_per_worker_bytes"] for r in data["results"]) / 1024 ** 2
        assert MEASURED_PEAK_MIB_PER_WORKER[benchmark[0]] == math.ceil(worst)

    def test_benchmark_ran_at_the_cpu_limit_it_is_labelled_with(self, benchmark, data):
        assert f"cpus{MEASURED_AT_CPUS}_" in benchmark[1].name
        assert max(r["workers"] for r in data["results"]) == MEASURED_AT_CPUS

    def test_every_algorithm_has_a_benchmark(self):
        from common.docking_algorithm import DOCKING_ALGORITHMS
        assert {a for a, _ in self.BENCHMARKS} == set(DOCKING_ALGORITHMS) == set(MEASURED_BEST_WORKERS)
