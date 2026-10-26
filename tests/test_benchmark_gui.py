"""Tests for benchmark_gui.py (Stage 16.0) -- no docker needed."""

import json

import pytest

import benchmark_docking
import benchmark_gui

MIB = 1024 ** 2


class TestSampleContainer:
    def test_summary_without_per_worker_field(self, monkeypatch):
        readings = iter([(100 * MIB, 2.0), (300 * MIB, 10.0)] + [(200 * MIB, 6.0)] * 1000)
        monkeypatch.setattr(benchmark_docking, "read_container_stats", lambda name: next(readings))
        stats = benchmark_gui.sample_container("mcp-ui", duration=0.1, interval=0.01)
        assert stats["n_samples"] >= 2
        assert stats["peak_mem_bytes"] == 300 * MIB
        assert stats["peak_cpu_percent"] == 10.0
        assert stats["sample_errors"] == 0
        assert "peak_mem_per_worker_bytes" not in stats
        assert "last_sample_error" not in stats

    def test_failed_samples_are_reported(self, monkeypatch):
        def failing(name):
            raise RuntimeError("docker stats failed for 'gone': No such container")

        monkeypatch.setattr(benchmark_docking, "read_container_stats", failing)
        stats = benchmark_gui.sample_container("gone", duration=0.05, interval=0.01)
        assert stats["n_samples"] == 0
        assert stats["sample_errors"] >= 1
        assert "No such container" in stats["last_sample_error"]
        assert benchmark_gui.format_summary(stats).startswith("no samples")


class TestFormatSummary:
    def test_cpu_and_memory(self):
        stats = benchmark_docking.summarize_samples([(100 * MIB, 4.0), (300 * MIB, 8.0)], 0, 1)
        stats["sample_errors"] = 0
        line = benchmark_gui.format_summary(stats)
        assert "CPU: mean 6.0%, peak 8.0%" in line
        assert "memory: mean 200.0 MiB, peak 300.0 MiB" in line
        assert "(2 sample(s))" in line


class TestAppendJson:
    def test_appends_records(self, tmp_path):
        out = tmp_path / "perf.json"
        benchmark_gui.append_json(out, {"label": "a"})
        benchmark_gui.append_json(out, {"label": "b"})
        assert [r["label"] for r in json.loads(out.read_text())] == ["a", "b"]

    def test_corrupt_file_is_not_overwritten(self, tmp_path):
        out = tmp_path / "perf.json"
        out.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            benchmark_gui.append_json(out, {"label": "a"})
        assert out.read_text() == "not json"


class TestSampleOnCue:
    @staticmethod
    def _fake_stats(monkeypatch):
        monkeypatch.setattr(benchmark_docking, "read_container_stats", lambda name: (100 * MIB, 5.0))

    def test_one_record_per_window_and_lines_echoed(self, monkeypatch):
        import io
        self._fake_stats(monkeypatch)
        out = io.StringIO()
        lines = ["setup\n", "SAMPLE-START running-25\n", "  show history: 900 ms\n",
                 "SAMPLE-STOP running-25\n", "SAMPLE-START idle\n", "SAMPLE-STOP idle\n"]
        records = benchmark_gui.sample_on_cue(lines, "mcp-ui", 0.01, echo=out)
        assert [r["label"] for r in records] == ["running-25", "idle"]
        assert all("seconds" in r and "started_at" in r and "n_samples" in r["stats"] for r in records)
        assert "show history: 900 ms" in out.getvalue()
        assert "[running-25]" in out.getvalue()

    @pytest.mark.parametrize("lines, message", [
        (["SAMPLE-STOP a\n"], "without a matching"),
        (["SAMPLE-START a\n", "SAMPLE-STOP b\n"], "without a matching"),
        (["SAMPLE-START a\n", "SAMPLE-START b\n"], "still sampling"),
        (["SAMPLE-START a\n"], "input ended"),
    ])
    def test_mismatched_markers_raise_and_stop_the_sampler(self, monkeypatch, lines, message):
        import threading
        self._fake_stats(monkeypatch)
        with pytest.raises(ValueError, match=message):
            benchmark_gui.sample_on_cue(lines, "mcp-ui", 0.01)
        # A leaked sampler would keep calling the real `docker stats` after
        # the monkeypatch is undone, inside other tests.
        assert not [t for t in threading.enumerate() if t.name == "container-stats"]

    def test_windows_finished_before_a_bad_cue_are_kept(self, monkeypatch):
        # Review Stage R1 (Phase 16): a driver dying mid-window used to
        # throw away every finished record, so --json-out wrote nothing.
        self._fake_stats(monkeypatch)
        lines = ["SAMPLE-START running-25\n", "SAMPLE-STOP running-25\n", "SAMPLE-START idle\n"]
        with pytest.raises(benchmark_gui.CueError, match="input ended") as err:
            benchmark_gui.sample_on_cue(lines, "mcp-ui", 0.01)
        assert [r["label"] for r in err.value.records] == ["running-25"]

    def test_main_saves_finished_windows_then_fails(self, monkeypatch, tmp_path):
        import io
        import sys
        self._fake_stats(monkeypatch)
        monkeypatch.setattr(benchmark_gui, "read_container_stats", lambda name: (100 * MIB, 5.0))
        out = tmp_path / "perf.json"
        monkeypatch.setattr(sys, "stdin", io.StringIO("SAMPLE-START a\nSAMPLE-STOP a\nSAMPLE-START b\n"))
        monkeypatch.setattr(sys, "argv", ["benchmark_gui.py", "--from-driver", "--interval", "0.01",
                                          "--json-out", str(out)])
        with pytest.raises(SystemExit, match="ended badly"):
            benchmark_gui.main()
        assert [r["label"] for r in json.loads(out.read_text())] == ["a"]


class TestDriverHelpers:
    def test_parse_perf_line(self):
        import benchmark_gui_browser as drv
        line = "[perf] render #7 mode=box_picker atoms=4421 render=38.2ms frame=51.0ms"
        assert drv.parse_perf_line(line) == {"n": 7, "mode": "box_picker", "atoms": 4421,
                                             "render_ms": 38.2, "frame_ms": 51.0}
        assert drv.parse_perf_line("[lat] 120 ms  button") is None

    def test_summarize_renders(self):
        import benchmark_gui_browser as drv
        assert drv.summarize_renders([]) == {"renders": 0}
        renders = [{"render_ms": r, "frame_ms": r + 10, "atoms": 5} for r in (10.0, 30.0, 20.0)]
        assert drv.summarize_renders(renders) == {"renders": 3, "median_render_ms": 20.0,
                                                  "median_frame_ms": 30.0, "atoms": 5}

    @pytest.mark.parametrize("argv, gui, url", [
        ([], "nicegui", "http://localhost:3001"),
        (["--gui", "streamlit"], "streamlit", "http://localhost:3000"),
        (["--gui", "nicegui", "--url", "http://ui:9000"], "nicegui", "http://ui:9000"),
    ])
    def test_gui_picks_the_default_url(self, argv, gui, url):
        import benchmark_gui_browser as drv
        args = drv.parse_args(argv + ["--out", "x.json"])
        assert (args.gui, args.url) == (gui, url)

    def test_unknown_gui_is_rejected(self):
        import benchmark_gui_browser as drv
        with pytest.raises(SystemExit):
            drv.parse_args(["--gui", "tk", "--out", "x.json"])

    def test_only_nicegui_filters_quasar_animations(self):
        import benchmark_gui_browser as drv
        assert drv.make_driver("streamlit", page=_FakePage()).latency_ignore == []
        assert ".q-ripple" in drv.make_driver("nicegui", page=_FakePage()).latency_ignore

    def test_format_timed_shows_the_filtered_median_only_when_it_differs(self):
        import benchmark_gui_browser as drv
        viewer = {"renders_per_action": 1.0}
        same = drv.format_timed("box", {"median_ms": 500, "median_filtered_ms": 500, "n": 10, "viewer": viewer})
        assert "filtered" not in same
        differs = drv.format_timed("box", {"median_ms": 700, "median_filtered_ms": 150, "n": 10, "viewer": viewer})
        assert "median 700 ms (n=10), filtered 150 ms" in differs


class _FakePage:
    def on(self, event, handler):
        pass
