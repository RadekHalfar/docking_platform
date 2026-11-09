# -*- coding: utf-8 -*-
"""
Benchmark sequential vs. parallel Moldina docking.

Reuses already-prepared receptor/ligand PDBQT files from a previous job's
workflow_results.json (orchestrator_output/<job_id>/) so the benchmark
measures only docking time, not ligand/protein preparation. It calls the
moldina_runner MCP service directly (the same run_docking tool the
orchestrator itself calls), so no platform code changes are needed to run
this — just a running `docker compose up` stack.

It also probes moldina-runner's actual CPU budget: both what Python's
os.cpu_count() reports *inside* the container, and the container's real
cgroup CPU quota. A mismatch between the two is a likely explanation for
parallel docking being much slower than expected instead of faster: Docker
Compose's `deploy.resources.limits.cpus` is a scheduling quota, not a CPU
mask, so os.cpu_count() inside the container can report far more cores than
the quota actually allows. max_workers is clamped against os.cpu_count()
(services/moldina_runner/__init__.py), so if that number is inflated,
concurrent workers can end up heavily contending for a much smaller real
CPU budget than they think they have — each one slower than it would be
running alone, not faster.

With --sample-stats it also samples the container's memory and CPU%
(`docker stats --no-stream`) on a background thread while each
configuration runs, and reports peak/mean memory plus peak memory per
worker: (peak - idle baseline) / min(workers, num_ligands). Each
`docker stats --no-stream` call itself takes ~1-2 s, so the sampled peak is
a lower bound of the true peak, not an exact value. ContainerStatsSampler is
container-agnostic and importable, so other measurements can reuse it
(benchmark_gui.py samples the GUI container with it).

Prerequisites:
- Run on the host, from the repo root, with Python 3.8+ and `requests`
  installed (`common.mcp_client` needs it) — use `python3`, since bare
  `python` may be Python 2. `docker` must be on PATH for the CPU probe and
  --sample-stats (both shell out to `docker exec` / `docker stats`).
- The Docker Compose stack is running (`docker compose up -d`), specifically
  moldina-runner (port 8004 published to the host).
- An existing job under orchestrator_output/ that completed at least the
  prepare_ligands and prepare_protein steps (it does not need to have
  completed docking itself).

Usage:
    python3 benchmark_docking.py --job-id job_20260730_071828_e45eb3
    python3 benchmark_docking.py --job-id JOB_ID --num-ligands 10 --worker-counts 2,4,8
    python3 benchmark_docking.py --job-id JOB_ID --center 10 20 30 --box 25 30 35
    python3 benchmark_docking.py --job-id JOB_ID --worker-counts 2,4,8 --sample-stats --json-out bench.json

Every configuration re-docks the SAME ligand set, so total run time is
roughly (1 sequential baseline + one run per --worker-counts entry) times
however long docking that many ligands takes. Defaults to 25 ligands across
worker counts 2,4,5,8,10 (7 configurations total, including the "1" worker
parallel run as a sanity check against the sequential baseline) — reduce
--num-ligands or --worker-counts for a faster first look.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM, DOCKING_ALGORITHMS
from common.mcp_client import call_mcp_tool

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_BASE_HOST = REPO_ROOT / "orchestrator_output"
CONTAINER_OUTPUT_BASE = "/app/orchestrator_output"
DEFAULT_URL = "http://localhost:8004"
DEFAULT_CONTAINER_NAME = "mcp-moldina-runner"

MIB = 1024 ** 2
STATS_FORMAT = "{{.MemUsage}}|{{.CPUPerc}}"
DOCKER_STATS_TIMEOUT = 15
# What read_container_stats() raises when a sample can't be taken: docker
# missing, a hung call, a non-zero exit, unparseable output.
STATS_ERRORS = (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError)

# docker stats prints binary units (MiB/GiB); some engines print decimal ones (MB/GB).
_SIZE_UNITS = {
    "B": 1,
    "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
    "kB": 1000, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
}
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$")


def parse_size_to_bytes(text: str) -> int:
    """'35.25MiB' -> bytes. Raises ValueError on an unknown unit or format."""
    match = _SIZE_RE.match(text)
    if not match or match.group(2) not in _SIZE_UNITS:
        raise ValueError(f"Unrecognised size: {text!r}")
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2)])


def parse_stats_line(line: str) -> Tuple[int, float]:
    """Parse one `docker stats` line in STATS_FORMAT, e.g.
    '35.25MiB / 8GiB|0.02%' -> (memory used in bytes, CPU percent).

    Raises ValueError on malformed input, including the '--' docker prints
    for a container that isn't running."""
    mem_part, sep, cpu_part = line.strip().partition("|")
    if not sep:
        raise ValueError(f"Unrecognised docker stats line: {line!r}")
    used, slash, _limit = mem_part.partition("/")
    if not slash:
        raise ValueError(f"Unrecognised memory usage: {mem_part!r}")
    cpu_text = cpu_part.strip()
    if not cpu_text.endswith("%"):
        raise ValueError(f"Unrecognised CPU percent: {cpu_part!r}")
    return parse_size_to_bytes(used), float(cpu_text[:-1])


def summarize_samples(samples: List[Tuple[int, float]], idle_bytes: int,
                      effective_workers: int) -> Dict[str, float]:
    """Peak/mean memory and CPU over (mem_bytes, cpu_percent) samples.

    Per-worker memory subtracts the idle container's baseline before
    dividing, so the runner's own fixed footprint isn't charged to workers.
    No samples -> just {"n_samples": 0}; no zeros are made up."""
    if not samples:
        return {"n_samples": 0}
    mems = [m for m, _ in samples]
    cpus = [c for _, c in samples]
    peak = max(mems)
    return {
        "n_samples": len(samples),
        "peak_mem_bytes": peak,
        "mean_mem_bytes": sum(mems) / len(mems),
        "peak_mem_per_worker_bytes": max(peak - idle_bytes, 0) / effective_workers,
        "peak_cpu_percent": max(cpus),
        "mean_cpu_percent": sum(cpus) / len(cpus),
    }


def read_container_stats(container_name: str) -> Tuple[int, float]:
    """One `docker stats --no-stream` sample -> (memory bytes, CPU percent)."""
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", STATS_FORMAT, container_name],
        capture_output=True, text=True, timeout=DOCKER_STATS_TIMEOUT,
    )
    if out.returncode != 0:
        raise RuntimeError(f"docker stats failed for '{container_name}': {out.stderr.strip()}")
    return parse_stats_line(out.stdout)


class ContainerStatsSampler:
    """Sample a container's memory/CPU% on a background thread.

        with ContainerStatsSampler("mcp-moldina-runner", interval=1.0) as s:
            do_work()
        s.summary(idle_bytes, effective_workers)

    A failed sample (docker missing, container gone, unparseable output) is
    counted in `errors` with its reason kept in `last_error`, so callers can
    report how trustworthy the numbers are instead of silently getting fewer
    samples.

    NOTE: polled peaks
    are lower bounds. The kernel records the exact one (cgroup v2
    memory.peak, readable in moldina-runner), but only per container
    lifetime unless reset per open file (kernel 6.12+), so it can't be
    attributed to one configuration by a plain read. If the per-worker
    figure ever decides a limit (e.g. a memory-based worker cap) rather
    than a caption, hold memory.peak open in a `docker exec` per
    configuration, reset it at the start and read it at the end."""

    def __init__(self, container_name: str, interval: float = 1.0):
        self.container_name = container_name
        self.interval = interval
        self.samples: List[Tuple[int, float]] = []
        self.errors = 0
        self.last_error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(read_container_stats(self.container_name))
            except STATS_ERRORS as e:
                self.errors += 1
                self.last_error = str(e)
            self._stop.wait(self.interval)

    def start(self) -> "ContainerStatsSampler":
        self._thread = threading.Thread(target=self._run, name="container-stats", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def __enter__(self) -> "ContainerStatsSampler":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def summary(self, idle_bytes: int, effective_workers: int) -> Dict[str, float]:
        stats = summarize_samples(self.samples, idle_bytes, effective_workers)
        stats["sample_errors"] = self.errors
        if self.last_error:
            stats["last_sample_error"] = self.last_error
        return stats


def load_job_files(job_id: str, num_ligands: Optional[int]) -> Tuple[str, List[str]]:
    """Read a job's workflow_results.json and return (receptor_pdbqt_path,
    [ligand_pdbqt_paths]) — both already in container-side path form, exactly
    as the orchestrator itself passes them to run_docking."""
    results_path = OUTPUT_BASE_HOST / job_id / "workflow_results.json"
    if not results_path.exists():
        raise SystemExit(f"No workflow_results.json found for job '{job_id}' at {results_path}")

    data = json.loads(results_path.read_text())
    step_results = data.get("results", {})

    proteins = (step_results.get("prepare_protein") or {}).get("prepared_proteins") or []
    if not proteins:
        raise SystemExit(
            f"Job '{job_id}' has no prepared protein in its results — "
            "pick a job that completed the prepare_protein step."
        )
    receptor_path = proteins[0]["pdbqt_path"]

    ligands = (step_results.get("prepare_ligands") or {}).get("prepared_ligands") or []
    if not ligands:
        raise SystemExit(
            f"Job '{job_id}' has no prepared ligands in its results — "
            "pick a job that completed the prepare_ligands step."
        )

    ligand_paths = [l["pdbqt_path"] for l in ligands if l.get("pdbqt_path")]
    if num_ligands is not None:
        ligand_paths = ligand_paths[:num_ligands]

    return receptor_path, ligand_paths


def load_center_box(job_id: str):
    """Best-effort: read center/box from a prior docking run's center.txt."""
    center_file = OUTPUT_BASE_HOST / job_id / "docking_results" / "center.txt"
    if not center_file.exists():
        return None

    values = {}
    for line in center_file.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            try:
                values[key.strip()] = float(value.strip())
            except ValueError:
                pass

    required = ["center_x", "center_y", "center_z", "size_x", "size_y", "size_z"]
    if not all(k in values for k in required):
        return None

    center = (values["center_x"], values["center_y"], values["center_z"])
    box = (values["size_x"], values["size_y"], values["size_z"])
    return center, box


def probe_container_cpu(container_name: str) -> None:
    """Compare os.cpu_count() as seen inside moldina-runner against its
    actual cgroup CPU quota — see module docstring for why this matters."""
    print("\n--- Container CPU diagnostics ---")
    try:
        cpu_count_out = subprocess.run(
            ["docker", "exec", container_name, "python", "-c", "import os; print(os.cpu_count())"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"Could not exec into '{container_name}' (is 'docker' on PATH? is the container running?): {e}")
        return

    if cpu_count_out.returncode == 0:
        print(f"os.cpu_count() inside {container_name}: {cpu_count_out.stdout.strip()}")
    else:
        print(f"Could not read os.cpu_count() inside {container_name}: {cpu_count_out.stderr.strip()}")

    # cgroup v2
    quota_out = subprocess.run(
        ["docker", "exec", container_name, "sh", "-c", "cat /sys/fs/cgroup/cpu.max 2>/dev/null"],
        capture_output=True, text=True, timeout=10,
    )
    quota = quota_out.stdout.strip()
    if quota and quota != "max max" and " " in quota:
        quota_us, period_us = quota.split()
        try:
            effective = float(quota_us) / float(period_us)
            print(f"cgroup v2 cpu.max: {quota}  ->  effective CPU budget ~= {effective:.2f} cores")
        except ValueError:
            print(f"cgroup v2 cpu.max: {quota}")
        return

    # cgroup v1 fallback
    v1_out = subprocess.run(
        ["docker", "exec", container_name, "sh", "-c",
         "cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null; echo /; cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null"],
        capture_output=True, text=True, timeout=10,
    )
    parts = v1_out.stdout.strip().split("/")
    if len(parts) == 2 and parts[0].strip():
        quota_us, period_us = parts[0].strip(), parts[1].strip()
        try:
            if float(quota_us) > 0:
                effective = float(quota_us) / float(period_us)
                print(f"cgroup v1 cfs_quota/period: {quota_us}/{period_us}  ->  effective CPU budget ~= {effective:.2f} cores")
            else:
                print("cgroup v1 cfs_quota_us is -1 (unlimited) — no CPU quota enforced by cgroups here.")
        except ValueError:
            print(f"cgroup v1 cfs_quota/period (unparsed): {quota_us}/{period_us}")
    else:
        print(
            "Could not read cgroup CPU quota (v1 or v2) — resource limits may not be enforced, "
            "or 'deploy.resources.limits' isn't honored by your Compose setup."
        )
    print("(compare the effective CPU budget above against the worker counts benchmarked below)")


def format_stats(stats: Dict[str, float]) -> str:
    """One-line human summary of a ContainerStatsSampler.summary() dict."""
    errors = stats.get("sample_errors", 0)
    error_note = f", {errors} failed sample(s): {stats.get('last_sample_error')}" if errors else ""
    if not stats["n_samples"]:
        return f"memory: no samples{error_note}"
    return (f"memory: peak {stats['peak_mem_bytes'] / MIB:.1f} MiB, "
            f"mean {stats['mean_mem_bytes'] / MIB:.1f} MiB, "
            f"peak/worker {stats['peak_mem_per_worker_bytes'] / MIB:.1f} MiB   "
            f"CPU: peak {stats['peak_cpu_percent']:.0f}%, mean {stats['mean_cpu_percent']:.0f}%   "
            f"({stats['n_samples']} sample(s){error_note})")


def format_stats_columns(stats: Dict[str, float]) -> str:
    """The summary table's three memory columns ('-' when nothing was sampled)."""
    if not stats["n_samples"]:
        return f"{'-':>12}{'-':>12}{'-':>12}"
    return (f"{stats['peak_mem_bytes'] / MIB:>12.1f}{stats['mean_mem_bytes'] / MIB:>12.1f}"
            f"{stats['peak_mem_per_worker_bytes'] / MIB:>12.1f}")


def run_docking(url, receptor, ligands, center, box_size, output_dir,
                 parallel, max_workers, exhaustiveness, population,
                 num_modes, energy_range, timeout, docking_algorithm=DEFAULT_DOCKING_ALGORITHM, cpu=1) -> dict:
    arguments = {
        "receptor_path": receptor,
        "ligand_paths": ligands,
        "output_dir": output_dir,
        "center": list(center),
        "box_size": list(box_size),
        "cpu": cpu,
        "population": population,  # PSO only; the server leaves it out of a Vina command
        "exhaustiveness": exhaustiveness,
        "num_modes": num_modes,
        "energy_range": energy_range,
        "parallel": parallel,
        "max_workers": max_workers,
        "docking_algorithm": docking_algorithm,
    }
    return call_mcp_tool(url, "run_docking", arguments, timeout=timeout)


def timeout_per_ligand(args: argparse.Namespace) -> int:
    """--timeout-per-ligand, or a default that covers the slowest
    configuration, the sequential one. Vina's search grows with
    exhaustiveness (~62 s per ligand at 8, ~2x at 16), so a flat 180 s would
    cut off a sequential Vina row at higher settings (the same budget as
    the orchestrator's docking_timeout_seconds(), with PSO's floor kept at
    180 s)."""
    if args.timeout_per_ligand is not None:
        return args.timeout_per_ligand
    if args.docking_algorithm == "vina":
        return max(180, 30 * args.exhaustiveness)
    return 180


def workers_ran(requested: int, result: Optional[dict]) -> int:
    """How many ligands the server really docked at a time. It clamps
    max_workers to its CPU quota // CPUs per ligand and to the ligand count,
    and reports the result as ``workers``; a row labelled with the request
    would, at --cpu 4, record parallel_8 and parallel_10 rows that both ran
    2 workers. A failed call, or a server that doesn't report
    ``workers``, falls back to the request."""
    ran = (result or {}).get("workers", requested)
    if ran != requested:
        print(f"  NOTE: the server ran {ran} worker(s), not {requested} "
              "(clamped to its CPU quota // CPUs per ligand, or to the ligand count)")
    return ran


def run_timed(call: Callable[[], dict],
              sampler: Optional[ContainerStatsSampler] = None) -> Tuple[Optional[dict], Optional[str], float]:
    """Run one configuration, sampled by *sampler* if given.

    Returns (result, error, wall seconds); a failed call gives (None, its
    message, ...) so the benchmark reports it and moves on. The wall time
    is taken before the sampler stops: stopping joins its thread, which can
    wait out an in-flight `docker stats` call (~1-2 s), and that is not
    docking time."""
    with sampler or contextlib.nullcontext():
        start = time.monotonic()
        try:
            result, error = call(), None
        except Exception as e:  # noqa: BLE001 - report and continue to the next configuration
            result, error = None, str(e)
        elapsed = time.monotonic() - start
    return result, error, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark sequential vs. parallel Moldina docking using "
        "already-prepared files from a previous job.",
    )
    parser.add_argument("--job-id", required=True, help="Job ID to reuse prepared receptor/ligand files from")
    parser.add_argument("--center", type=float, nargs=3, metavar=("X", "Y", "Z"), default=None,
                         help="Search box center (auto-detected from the job's docking_results/center.txt if omitted)")
    parser.add_argument("--box", type=float, nargs=3, metavar=("X", "Y", "Z"), default=None,
                         help="Search box size (auto-detected if omitted)")
    parser.add_argument("--num-ligands", type=int, default=25,
                         help="Number of ligands to use from the job (default: 25 - every configuration re-docks this set)")
    parser.add_argument("--worker-counts", type=str, default="2,4,5,8,10",
                         help="Comma-separated worker counts to benchmark in parallel mode (default: 2,4,5,8,10)")
    parser.add_argument("--exhaustiveness", type=int, default=8)
    parser.add_argument("--population", type=int, default=500, help="PSO population (Moldina mode only)")
    parser.add_argument("--cpu", type=int, default=1,
                         help="CPUs per ligand (default: 1). Vina mode's Monte Carlo search is multithreaded; "
                              "PSO ignores it. The server clamps workers to its CPU quota // cpu")
    parser.add_argument("--docking-algorithm", choices=DOCKING_ALGORITHMS, default=DEFAULT_DOCKING_ALGORITHM,
                         help="Search to benchmark: 'moldina' (PSO) or 'vina' (Monte Carlo) (default: moldina)")
    parser.add_argument("--num-modes", type=int, default=15)
    parser.add_argument("--energy-range", type=float, default=100.0)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"moldina_runner MCP URL (default: {DEFAULT_URL})")
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER_NAME,
                         help=f"Docker container name for CPU diagnostics (default: {DEFAULT_CONTAINER_NAME})")
    parser.add_argument("--skip-cpu-diagnostics", action="store_true", help="Skip the docker exec CPU/cgroup probe")
    parser.add_argument("--timeout-per-ligand", type=int, default=None,
                         help="HTTP timeout budget per ligand, in seconds (default: 180 for PSO; "
                              "for Vina max(180, 30 x exhaustiveness), since its search grows with it)")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path to also write results as JSON")
    parser.add_argument("--sample-stats", action="store_true",
                         help="Sample the container's memory/CPU%% with `docker stats` during each "
                              "configuration and report peak/mean memory and peak memory per worker")
    parser.add_argument("--stats-interval", type=float, default=1.0,
                         help="Seconds between --sample-stats samples, on top of the ~1-2 s each "
                              "`docker stats` call takes (default: 1.0)")
    args = parser.parse_args()

    receptor, ligands = load_job_files(args.job_id, args.num_ligands)
    print(f"Receptor: {receptor}")
    print(f"Ligands:  {len(ligands)} from job '{args.job_id}'")

    if args.center and args.box:
        center, box = tuple(args.center), tuple(args.box)
    else:
        detected = load_center_box(args.job_id)
        if detected is None:
            raise SystemExit(
                "Could not auto-detect center/box from this job (no docking_results/center.txt). "
                "Pass --center X Y Z --box X Y Z explicitly."
            )
        center, box = detected
        print(f"Center/box auto-detected from job's previous docking run: center={center} box={box}")

    if not args.skip_cpu_diagnostics:
        probe_container_cpu(args.container_name)

    idle_bytes = None
    if args.sample_stats:
        try:
            idle_bytes, idle_cpu = read_container_stats(args.container_name)
        except STATS_ERRORS as e:
            raise SystemExit(f"--sample-stats: could not read the idle baseline from "
                             f"'{args.container_name}' ({e}). Is 'docker' on PATH and the container running?")
        print(f"\nIdle baseline of {args.container_name}: {idle_bytes / MIB:.1f} MiB, {idle_cpu:.2f}% CPU")

    worker_counts = [int(w.strip()) for w in args.worker_counts.split(",") if w.strip()]
    configs = [("sequential", False, 1)] + [(f"parallel_{w}", True, w) for w in worker_counts]

    run_id = uuid.uuid4().hex[:8]
    timeout = max(300, timeout_per_ligand(args) * len(ligands))

    print(f"\nBenchmarking {len(ligands)} ligand(s) with docking_algorithm={args.docking_algorithm} cpu={args.cpu} "
          f"across {len(configs)} configuration(s): "
          f"{', '.join(label for label, *_ in configs)}\n")

    rows = []
    baseline_seconds = None
    for label, parallel, workers in configs:
        output_dir = f"{CONTAINER_OUTPUT_BASE}/_benchmark_{run_id}/{label}"
        print(f"--- {label} (parallel={parallel}, max_workers={workers}) ---")
        sampler = ContainerStatsSampler(args.container_name, args.stats_interval) if args.sample_stats else None
        result, error, elapsed = run_timed(
            lambda: run_docking(
                args.url, receptor, ligands, center, box, output_dir,
                parallel, workers, args.exhaustiveness, args.population,
                args.num_modes, args.energy_range, timeout, args.docking_algorithm, args.cpu,
            ),
            sampler,
        )

        ran = workers_ran(workers, result)
        if error is not None:
            row = {"label": label, "workers": workers, "error": error}
        else:
            if baseline_seconds is None:
                baseline_seconds = elapsed
            row = {
                "label": label, "workers": ran, "elapsed_seconds": elapsed,
                "seconds_per_ligand": elapsed / len(ligands) if ligands else 0.0,
                "speedup_vs_sequential": baseline_seconds / elapsed if elapsed else 0.0,
                "successful": result.get("successful", 0), "failed": result.get("failed", 0),
            }
            if ran != workers:
                row["requested_workers"] = workers
        # Sampled for failed runs too: a memory-pressure failure is exactly when the numbers matter.
        if sampler:
            row["stats"] = sampler.summary(idle_bytes, max(1, min(ran, len(ligands))))
            print(f"  {format_stats(row['stats'])}")
        if error is not None:
            print(f"  FAILED: {error}\n")
        else:
            print(f"  wall time: {elapsed:.1f}s   ({row['seconds_per_ligand']:.1f}s/ligand)   "
                  f"successful={row['successful']} failed={row['failed']}   "
                  f"speedup vs sequential: {row['speedup_vs_sequential']:.2f}x\n")
        rows.append(row)

    width = 114 if args.sample_stats else 78
    stats_header = f"{'peak MiB':>12}{'mean MiB':>12}{'MiB/worker':>12}" if args.sample_stats else ""
    print("=" * width)
    print(f"{'config':<16}{'workers':>8}{'wall (s)':>12}{'s/ligand':>12}{'speedup':>10}{stats_header}")
    print("-" * width)
    for row in rows:
        if "error" in row:
            print(f"{row['label']:<16}{row['workers']:>8}   ERROR: {row['error']}")
        else:
            stats_cols = format_stats_columns(row["stats"]) if args.sample_stats else ""
            print(f"{row['label']:<16}{row['workers']:>8}{row['elapsed_seconds']:>12.1f}"
                  f"{row['seconds_per_ligand']:>12.1f}{row['speedup_vs_sequential']:>9.2f}x{stats_cols}")
    print("=" * width)
    if args.sample_stats:
        print("Memory is sampled (each `docker stats` call takes ~1-2 s), so peaks are lower bounds. "
              "MiB/worker = (peak - idle) / min(workers, ligands).")
    print(f"\nOutput written under orchestrator_output/_benchmark_{run_id}/ (safe to delete afterward).")

    if args.json_out:
        payload = {
            "job_id": args.job_id, "num_ligands": len(ligands),
            "center": center, "box_size": box, "run_id": run_id,
            "docking_algorithm": args.docking_algorithm,
            "cpu": args.cpu,
            "results": rows,
        }
        if args.sample_stats:
            payload.update({
                "container_name": args.container_name,
                "stats_interval": args.stats_interval,
                "idle_mem_bytes": idle_bytes,
            })
        args.json_out.write_text(json.dumps(payload, indent=2))
        print(f"Wrote JSON results to {args.json_out}")


if __name__ == "__main__":
    main()
