"""Tests for benchmark_docking.py's --sample-stats parsing and sampler (no docker needed)."""

import threading
import time

import pytest

import benchmark_docking as bench

MIB = 1024 ** 2


def _wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "sampler thread made no progress"
        time.sleep(0.005)


class TestParseSizeToBytes:
    @pytest.mark.parametrize("text,expected", [
        ("512B", 512),
        ("2KiB", 2048),
        ("35.25MiB", int(35.25 * MIB)),
        ("8GiB", 8 * 1024 ** 3),
        ("1TiB", 1024 ** 4),
        ("3kB", 3000),
        ("1.5MB", 1_500_000),
        ("2GB", 2_000_000_000),
        (" 10MiB ", 10 * MIB),
    ])
    def test_units(self, text, expected):
        assert bench.parse_size_to_bytes(text) == expected

    @pytest.mark.parametrize("text", ["", "MiB", "12", "12XB", "--", "1,5MiB"])
    def test_garbage_raises(self, text):
        with pytest.raises(ValueError):
            bench.parse_size_to_bytes(text)


class TestParseStatsLine:
    def test_real_docker_stats_line(self):
        # Captured live from `docker stats --no-stream --format ... mcp-moldina-runner`.
        mem, cpu = bench.parse_stats_line("35.25MiB / 8GiB|0.02%\n")
        assert mem == int(35.25 * MIB)
        assert cpu == pytest.approx(0.02)

    def test_multi_core_cpu_percent(self):
        assert bench.parse_stats_line("1.2GiB / 8GiB|412.50%")[1] == pytest.approx(412.5)

    @pytest.mark.parametrize("line", [
        "-- / --|--",           # container not running
        "35.25MiB / 8GiB",      # no CPU field
        "35.25MiB|0.02%",       # no memory limit
        "35.25MiB / 8GiB|0.02", # no percent sign
        "",
    ])
    def test_malformed_raises(self, line):
        with pytest.raises(ValueError):
            bench.parse_stats_line(line)


class TestSummarizeSamples:
    def test_empty_reports_no_samples_only(self):
        assert bench.summarize_samples([], idle_bytes=0, effective_workers=1) == {"n_samples": 0}

    def test_peak_mean_and_per_worker(self):
        samples = [(100 * MIB, 50.0), (500 * MIB, 390.0), (300 * MIB, 200.0)]
        stats = bench.summarize_samples(samples, idle_bytes=100 * MIB, effective_workers=4)
        assert stats["n_samples"] == 3
        assert stats["peak_mem_bytes"] == 500 * MIB
        assert stats["mean_mem_bytes"] == pytest.approx(300 * MIB)
        assert stats["peak_mem_per_worker_bytes"] == pytest.approx(100 * MIB)  # (500 - 100) / 4
        assert stats["peak_cpu_percent"] == 390.0
        assert stats["mean_cpu_percent"] == pytest.approx(640 / 3)

    def test_peak_below_idle_clamps_to_zero(self):
        stats = bench.summarize_samples([(30 * MIB, 0.0)], idle_bytes=35 * MIB, effective_workers=1)
        assert stats["peak_mem_per_worker_bytes"] == 0


class TestContainerStatsSampler:
    def test_collects_samples_until_stopped(self, monkeypatch):
        calls = []

        def fake_read(name):
            calls.append(name)
            return (len(calls) * MIB, 10.0)

        monkeypatch.setattr(bench, "read_container_stats", fake_read)
        with bench.ContainerStatsSampler("mcp-ui", interval=0.01) as sampler:
            _wait_until(lambda: len(sampler.samples) >= 3)
        n = len(sampler.samples)
        assert n >= 3
        assert set(calls) == {"mcp-ui"}
        assert not sampler._thread.is_alive()
        summary = sampler.summary(idle_bytes=0, effective_workers=1)
        assert summary["n_samples"] == n
        assert summary["sample_errors"] == 0
        assert "last_sample_error" not in summary

    def test_failed_samples_are_counted_with_reason(self, monkeypatch):
        def failing_read(name):
            raise RuntimeError("docker stats failed for 'gone': No such container")

        monkeypatch.setattr(bench, "read_container_stats", failing_read)
        with bench.ContainerStatsSampler("gone", interval=0.01) as sampler:
            _wait_until(lambda: sampler.errors >= 2)
        summary = sampler.summary(idle_bytes=0, effective_workers=1)
        assert summary["n_samples"] == 0
        assert summary["sample_errors"] >= 2
        assert "No such container" in summary["last_sample_error"]
        assert "no samples" in bench.format_stats(summary)

    def test_unexpected_exception_is_not_swallowed(self, monkeypatch):
        # Only the known docker/parse failures are counted; a bug surfaces as a thread error.
        def buggy_read(name):
            raise KeyError("bug")

        raised = []
        monkeypatch.setattr(bench, "read_container_stats", buggy_read)
        monkeypatch.setattr(threading, "excepthook", raised.append)
        sampler = bench.ContainerStatsSampler("x", interval=0.01).start()
        sampler._thread.join(timeout=5)
        assert not sampler._thread.is_alive()
        assert sampler.errors == 0
        assert [args.exc_type for args in raised] == [KeyError]
        assert raised[0].thread.name == "container-stats"
        sampler.stop()


class _SlowStopSampler:
    """Stands in for ContainerStatsSampler whose stop() waits out an
    in-flight `docker stats` call."""

    def __init__(self, stop_seconds):
        self.stop_seconds = stop_seconds
        self.entered = self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        time.sleep(self.stop_seconds)
        self.exited = True


class TestRunTimed:
    def test_wall_time_excludes_sampler_shutdown(self):
        # The wall time is taken before sampler.stop(), so no in-flight
        # docker-stats call (~1-2 s) is counted as docking time.
        sampler = _SlowStopSampler(0.5)
        result, error, elapsed = bench.run_timed(lambda: {"successful": 1}, sampler)
        assert (result, error) == ({"successful": 1}, None)
        assert sampler.entered and sampler.exited
        assert elapsed < 0.25

    def test_failure_is_reported_not_raised(self):
        def boom():
            raise RuntimeError("MCP call failed")

        result, error, elapsed = bench.run_timed(boom, _SlowStopSampler(0))
        assert (result, error) == (None, "MCP call failed")
        assert elapsed >= 0

    def test_without_sampler(self):
        assert bench.run_timed(lambda: {"x": 1})[:2] == ({"x": 1}, None)


class TestFormatting:
    def test_columns_dash_without_samples(self):
        assert bench.format_stats_columns({"n_samples": 0}).split() == ["-", "-", "-"]

    def test_columns_in_mib(self):
        stats = bench.summarize_samples([(200 * MIB, 1.0)], idle_bytes=0, effective_workers=2)
        assert bench.format_stats_columns(stats).split() == ["200.0", "200.0", "100.0"]


class TestRunDockingArguments:
    """--docking-algorithm and --cpu are sent."""

    @staticmethod
    def _sent(monkeypatch, **kwargs) -> dict:
        sent = {}
        monkeypatch.setattr(bench, "call_mcp_tool",
                            lambda url, tool, arguments, timeout: sent.update(arguments) or {})
        bench.run_docking("http://x", "r", ["l"], (0, 0, 0), (20, 20, 20), "/out",
                          False, 1, 8, 500, 15, 100.0, 60, **kwargs)
        return sent

    @pytest.mark.parametrize("algorithm,cpu", [("moldina", 1), ("vina", 1), ("vina", 4)])
    def test_algorithm_and_cpu_are_sent(self, monkeypatch, algorithm, cpu):
        # population is sent in both modes: the server leaves it out of a
        # Vina command itself.
        sent = self._sent(monkeypatch, docking_algorithm=algorithm, cpu=cpu)
        assert (sent["docking_algorithm"], sent["cpu"], sent["population"]) == (algorithm, cpu, 500)


class TestWorkersRan:
    """Rows record the workers the server ran."""

    def test_reported_workers_win_and_are_noted(self, capsys):
        assert bench.workers_ran(8, {"workers": 2}) == 2
        assert "server ran 2 worker(s), not 8" in capsys.readouterr().out

    def test_matching_count_prints_nothing(self, capsys):
        assert bench.workers_ran(4, {"workers": 4}) == 4
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("result", [None, {"successful": 1}])
    def test_failed_call_or_old_server_falls_back_to_request(self, result):
        assert bench.workers_ran(6, result) == 6


class TestTimeoutPerLigand:
    """The default budget follows Vina's exhaustiveness."""

    @staticmethod
    def _args(**kw):
        import argparse
        base = {"timeout_per_ligand": None, "docking_algorithm": "moldina", "exhaustiveness": 8}
        return argparse.Namespace(**{**base, **kw})

    def test_pso_default_unchanged(self):
        assert bench.timeout_per_ligand(self._args(exhaustiveness=32)) == 180

    def test_vina_default_scales_with_exhaustiveness(self):
        assert bench.timeout_per_ligand(self._args(docking_algorithm="vina")) == 240
        assert bench.timeout_per_ligand(self._args(docking_algorithm="vina", exhaustiveness=32)) == 960

    def test_explicit_flag_wins(self):
        assert bench.timeout_per_ligand(self._args(docking_algorithm="vina", timeout_per_ligand=50)) == 50
