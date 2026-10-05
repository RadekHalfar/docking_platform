"""Tests for ui.worker_cap.detect_moldina_worker_cap (no Streamlit dependency)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ui.worker_cap import (
    MEASURED_BEST_WORKERS,
    MEASURED_PEAK_MIB_PER_WORKER,
    WorkerCap,
    default_worker_count,
    describe_worker_cap,
    detect_moldina_worker_cap,
)


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
        assert detect_moldina_worker_cap(compose_path) == WorkerCap(4, "compose")

    def test_truncates_fractional_cpu_limit(self, tmp_path):
        compose_path = _write_compose(tmp_path, "2.5")
        assert detect_moldina_worker_cap(compose_path) == WorkerCap(2, "compose")

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
        warning, caption = describe_worker_cap(WorkerCap(10, "compose"))
        assert warning is None
        assert caption.startswith("Docking service is limited to 10 CPUs (docker-compose.yml).")
        assert "memory usage" in caption
        assert _MEASURED_TEXT in caption

    def test_fallback_source_warns_with_reason(self):
        warning, caption = describe_worker_cap(WorkerCap(16, "fallback", "some reason"))
        assert "Couldn't read the docking service's CPU limit" in warning
        assert "The real limit may be lower." in warning
        assert "(some reason)" in warning
        assert "memory usage" in caption
        assert _MEASURED_TEXT in caption


_MEASURED_TEXT = (
    f"Benchmarked best: {MEASURED_BEST_WORKERS} workers "
    f"(up to ~{MEASURED_PEAK_MIB_PER_WORKER} MiB memory each"
)


class TestDefaultWorkerCount:
    @pytest.mark.parametrize("cap", [1, MEASURED_BEST_WORKERS - 1, MEASURED_BEST_WORKERS,
                                     MEASURED_BEST_WORKERS + 1, 64])
    def test_is_measured_best_clamped_to_cap(self, cap):
        assert default_worker_count(WorkerCap(cap, "compose")) == min(MEASURED_BEST_WORKERS, cap)

    def test_fallback_cap_is_clamped_too(self):
        assert default_worker_count(WorkerCap(2, "fallback", "r")) == 2
