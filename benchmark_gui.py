# -*- coding: utf-8 -*-
"""
Sample a GUI container's CPU% and memory over a fixed window.

This tool measures any container the same way: it reuses benchmark_docking.py's ContainerStatsSampler
(`docker stats --no-stream` on a background thread) for --duration seconds
and prints mean/peak CPU% and memory. Run it while the GUI is in the state
you want to measure -- e.g. a completed session loaded and left alone
("idle"), or a docking job running so the page polls ("running"). The
method and the recorded numbers are in docs/GUI_GUIDE.md, "Performance".

Each `docker stats --no-stream` call itself takes ~1-2 s, so samples are
polled and peaks are lower bounds, not exact values.

Prerequisites:
- Run on the host, from the repo root, with Python 3.8+ (`python3`, since
  bare `python` may be Python 2) and `docker` on PATH.
- The container to sample is running (default: mcp-ui, the GUI).

Usage:
    python3 benchmark_gui.py --label nicegui-idle-25lig
    python3 benchmark_gui.py --label nicegui-running-25lig --duration 120 --json-out gui_perf.json

--json-out appends one record per run to a JSON list, so one file collects
every scenario of a measurement session.

With --from-driver it samples on cue instead of for --duration: it reads
benchmark_gui_browser.py's output on stdin, echoes it, and samples between
each `SAMPLE-START <label>` and `SAMPLE-STOP <label>` line -- one record
per label. See benchmark_gui_browser.py for the pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, TextIO

from benchmark_docking import MIB, STATS_ERRORS, ContainerStatsSampler, read_container_stats

DEFAULT_CONTAINER_NAME = "mcp-ui"


def summarize_sampler(sampler: ContainerStatsSampler) -> Dict[str, float]:
    """The sampler's summary (peak/mean, error count, last error), without
    the per-worker field: a GUI container has no workers."""
    stats = sampler.summary(idle_bytes=0, effective_workers=1)
    stats.pop("peak_mem_per_worker_bytes", None)
    return stats


class CueError(ValueError):
    """The driver's SAMPLE-START/SAMPLE-STOP lines didn't pair up.
    *records* holds the windows that did finish, so they can still be saved."""

    def __init__(self, message: str, records: List[dict]) -> None:
        super().__init__(message)
        self.records = records


def sample_container(container_name: str, duration: float, interval: float) -> Dict[str, float]:
    """Sample *container_name* for *duration* seconds."""
    with ContainerStatsSampler(container_name, interval) as sampler:
        time.sleep(duration)
    return summarize_sampler(sampler)


def sample_on_cue(lines: Iterable[str], container_name: str, interval: float,
                  echo: Optional[TextIO] = None) -> List[dict]:
    """Echo *lines*; sample between `SAMPLE-START <label>` and
    `SAMPLE-STOP <label>`. Returns one {label, started_at, seconds, stats} per window.

    A STOP for another label, a second START, or input ending mid-window is
    an error in the driver, so it raises CueError instead of guessing; the
    error carries the windows finished before it."""
    records: List[dict] = []
    active = None  # (label, sampler, start time, start timestamp)
    try:
        for line in lines:
            if echo:
                echo.write(line)
                echo.flush()
            kind, _, label = line.strip().partition(" ")
            if kind == "SAMPLE-START":
                if active:
                    raise CueError(f"SAMPLE-START {label!r} while {active[0]!r} is still sampling", records)
                started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                active = (label, ContainerStatsSampler(container_name, interval).start(),
                          time.monotonic(), started_at)
            elif kind == "SAMPLE-STOP":
                if not active or active[0] != label:
                    raise CueError(f"SAMPLE-STOP {label!r} without a matching SAMPLE-START", records)
                name, sampler, started, started_at = active
                seconds = time.monotonic() - started
                sampler.stop()
                active = None
                records.append({"label": name, "started_at": started_at, "seconds": round(seconds, 1),
                                "stats": summarize_sampler(sampler)})
                if echo:
                    echo.write(f"  [{name}] {format_summary(records[-1]['stats'])}\n")
        if active:
            raise CueError(f"input ended while {active[0]!r} was still sampling", records)
    finally:
        # Never leave a sampler thread running `docker stats` behind an error.
        if active:
            active[1].stop()
    return records


def format_summary(stats: Dict[str, float]) -> str:
    errors = stats.get("sample_errors", 0)
    error_note = f", {errors} failed sample(s): {stats.get('last_sample_error')}" if errors else ""
    if not stats["n_samples"]:
        return f"no samples{error_note}"
    return (f"CPU: mean {stats['mean_cpu_percent']:.1f}%, peak {stats['peak_cpu_percent']:.1f}%   "
            f"memory: mean {stats['mean_mem_bytes'] / MIB:.1f} MiB, peak {stats['peak_mem_bytes'] / MIB:.1f} MiB   "
            f"({stats['n_samples']} sample(s){error_note})")


def append_json(path: Path, record: dict) -> None:
    records = json.loads(path.read_text()) if path.exists() else []
    records.append(record)
    path.write_text(json.dumps(records, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample a GUI container's CPU%% and memory over a fixed window.")
    parser.add_argument("--container", default=DEFAULT_CONTAINER_NAME,
                        help=f"Container to sample (default: {DEFAULT_CONTAINER_NAME})")
    parser.add_argument("--duration", type=float, default=60.0, help="Seconds to sample (default: 60)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between samples, on top of the ~1-2 s each `docker stats` call takes "
                             "(default: 1.0)")
    parser.add_argument("--label", default=None,
                        help="What was measured, e.g. nicegui-idle-25lig (required without --from-driver)")
    parser.add_argument("--from-driver", action="store_true",
                        help="Sample between the SAMPLE-START/SAMPLE-STOP lines benchmark_gui_browser.py "
                             "prints, read from stdin, instead of for --duration")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="Append this run's record to a JSON list in this file")
    args = parser.parse_args()

    if not args.from_driver and not args.label:
        parser.error("--label is required unless --from-driver is given")

    try:
        read_container_stats(args.container)
    except STATS_ERRORS as e:
        raise SystemExit(f"Could not sample '{args.container}' ({e}). "
                         "Is 'docker' on PATH and the container running?")

    measured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cue_error = None
    if args.from_driver:
        try:
            windows = sample_on_cue(sys.stdin, args.container, args.interval, echo=sys.stdout)
        except CueError as e:
            # E.g. the driver crashed mid-window: still save the windows
            # that finished (a running-job window can take half an hour).
            cue_error, windows = e, e.records
        records = [
            {"container": args.container, "interval_seconds": args.interval, "measured_at": measured_at, **r}
            for r in windows
        ]
    else:
        print(f"Sampling {args.container} for {args.duration:.0f} s ({args.label}) ...")
        stats = sample_container(args.container, args.duration, args.interval)
        print(format_summary(stats))
        records = [{"label": args.label, "container": args.container,
                    "seconds": args.duration, "interval_seconds": args.interval,
                    "measured_at": measured_at, "stats": stats}]
    print("Samples are polled (each `docker stats` call takes ~1-2 s), so peaks are lower bounds.")

    if args.json_out:
        for record in records:
            append_json(args.json_out, record)
        print(f"Appended {len(records)} record(s) to {args.json_out}")
    if cue_error:
        raise SystemExit(f"Driver output ended badly: {cue_error}")

if __name__ == "__main__":
    main()
