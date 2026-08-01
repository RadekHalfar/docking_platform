"""Tests for ui.worker_cap.detect_moldina_worker_cap (no Streamlit dependency)."""
from __future__ import annotations

from unittest.mock import patch

from ui.worker_cap import detect_moldina_worker_cap


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


class TestDetectMoldinaWorkerCap:
    def test_reads_configured_cpu_limit(self, tmp_path):
        compose_path = _write_compose(tmp_path, "4")
        assert detect_moldina_worker_cap(compose_path) == 4

    def test_truncates_fractional_cpu_limit(self, tmp_path):
        compose_path = _write_compose(tmp_path, "2.5")
        assert detect_moldina_worker_cap(compose_path) == 2

    def test_falls_back_to_cpu_count_when_file_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist.yml"
        with patch("ui.worker_cap.os.cpu_count", return_value=6):
            assert detect_moldina_worker_cap(missing) == 6

    def test_falls_back_to_cpu_count_when_no_limit_configured(self, tmp_path):
        compose_path = _write_compose(tmp_path, None)
        with patch("ui.worker_cap.os.cpu_count", return_value=3):
            assert detect_moldina_worker_cap(compose_path) == 3

    def test_falls_back_to_cpu_count_when_limit_is_zero_or_negative(self, tmp_path):
        compose_path = _write_compose(tmp_path, "0")
        with patch("ui.worker_cap.os.cpu_count", return_value=5):
            assert detect_moldina_worker_cap(compose_path) == 5

    def test_falls_back_to_cpu_count_on_malformed_yaml(self, tmp_path):
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("not: [valid: yaml: at: all")
        with patch("ui.worker_cap.os.cpu_count", return_value=2):
            assert detect_moldina_worker_cap(compose_path) == 2

    def test_never_raises_and_defaults_to_1_if_cpu_count_unknown(self, tmp_path):
        missing = tmp_path / "does_not_exist.yml"
        with patch("ui.worker_cap.os.cpu_count", return_value=None):
            assert detect_moldina_worker_cap(missing) == 1

    def test_defaults_to_repo_docker_compose_when_no_path_given(self):
        # Exercises the real default-path branch against this repo's actual
        # docker-compose.yml -- just needs to not raise and return a positive int.
        result = detect_moldina_worker_cap()
        assert isinstance(result, int)
        assert result >= 1
