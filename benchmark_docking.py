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

Prerequisites:
- The Docker Compose stack is running (`docker compose up -d`), specifically
  moldina-runner (port 8004 published to the host).
- An existing job under orchestrator_output/ that completed at least the
  prepare_ligands and prepare_protein steps (it does not need to have
  completed docking itself).

Usage:
    python benchmark_docking.py --job-id job_20260730_071828_e45eb3
    python benchmark_docking.py --job-id JOB_ID --num-ligands 10 --worker-counts 2,4,8
    python benchmark_docking.py --job-id JOB_ID --center 10 20 30 --box 25 30 35

Every configuration re-docks the SAME ligand set, so total run time is
roughly (1 sequential baseline + one run per --worker-counts entry) times
however long docking that many ligands takes. Defaults to 25 ligands across
worker counts 2,4,5,8,10 (7 configurations total, including the "1" worker
parallel run as a sanity check against the sequential baseline) — reduce
--num-ligands or --worker-counts for a faster first look.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from common.mcp_client import call_mcp_tool

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_BASE_HOST = REPO_ROOT / "orchestrator_output"
CONTAINER_OUTPUT_BASE = "/app/orchestrator_output"
DEFAULT_URL = "http://localhost:8004"
DEFAULT_CONTAINER_NAME = "mcp-moldina-runner"


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


def run_docking(url, receptor, ligands, center, box_size, output_dir,
                 parallel, max_workers, exhaustiveness, population,
                 num_modes, energy_range, timeout) -> dict:
    return call_mcp_tool(
        url, "run_docking",
        {
            "receptor_path": receptor,
            "ligand_paths": ligands,
            "output_dir": output_dir,
            "center": list(center),
            "box_size": list(box_size),
            "cpu": 1,
            "population": population,
            "exhaustiveness": exhaustiveness,
            "num_modes": num_modes,
            "energy_range": energy_range,
            "parallel": parallel,
            "max_workers": max_workers,
        },
        timeout=timeout,
    )


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
    parser.add_argument("--population", type=int, default=500)
    parser.add_argument("--num-modes", type=int, default=15)
    parser.add_argument("--energy-range", type=float, default=100.0)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"moldina_runner MCP URL (default: {DEFAULT_URL})")
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER_NAME,
                         help=f"Docker container name for CPU diagnostics (default: {DEFAULT_CONTAINER_NAME})")
    parser.add_argument("--skip-cpu-diagnostics", action="store_true", help="Skip the docker exec CPU/cgroup probe")
    parser.add_argument("--timeout-per-ligand", type=int, default=180,
                         help="HTTP timeout budget per ligand, in seconds (default: 180)")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path to also write results as JSON")
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

    worker_counts = [int(w.strip()) for w in args.worker_counts.split(",") if w.strip()]
    configs = [("sequential", False, 1)] + [(f"parallel_{w}", True, w) for w in worker_counts]

    run_id = uuid.uuid4().hex[:8]
    timeout = max(300, args.timeout_per_ligand * len(ligands))

    print(f"\nBenchmarking {len(ligands)} ligand(s) across {len(configs)} configuration(s): "
          f"{', '.join(label for label, *_ in configs)}\n")

    rows = []
    baseline_seconds = None
    for label, parallel, workers in configs:
        output_dir = f"{CONTAINER_OUTPUT_BASE}/_benchmark_{run_id}/{label}"
        print(f"--- {label} (parallel={parallel}, max_workers={workers}) ---")
        start = time.monotonic()
        try:
            result = run_docking(
                args.url, receptor, ligands, center, box, output_dir,
                parallel, workers, args.exhaustiveness, args.population,
                args.num_modes, args.energy_range, timeout,
            )
        except Exception as e:  # noqa: BLE001 - report and continue to the next configuration
            print(f"  FAILED: {e}\n")
            rows.append({"label": label, "workers": workers, "error": str(e)})
            continue
        elapsed = time.monotonic() - start

        successful = result.get("successful", 0)
        failed = result.get("failed", 0)
        per_ligand = elapsed / len(ligands) if ligands else 0.0
        if baseline_seconds is None:
            baseline_seconds = elapsed
        speedup = baseline_seconds / elapsed if elapsed else 0.0

        print(f"  wall time: {elapsed:.1f}s   ({per_ligand:.1f}s/ligand)   "
              f"successful={successful} failed={failed}   speedup vs sequential: {speedup:.2f}x\n")

        rows.append({
            "label": label, "workers": workers, "elapsed_seconds": elapsed,
            "seconds_per_ligand": per_ligand, "speedup_vs_sequential": speedup,
            "successful": successful, "failed": failed,
        })

    print("=" * 78)
    print(f"{'config':<16}{'workers':>8}{'wall (s)':>12}{'s/ligand':>12}{'speedup':>10}")
    print("-" * 78)
    for row in rows:
        if "error" in row:
            print(f"{row['label']:<16}{row['workers']:>8}   ERROR: {row['error']}")
        else:
            print(f"{row['label']:<16}{row['workers']:>8}{row['elapsed_seconds']:>12.1f}"
                  f"{row['seconds_per_ligand']:>12.1f}{row['speedup_vs_sequential']:>9.2f}x")
    print("=" * 78)
    print(f"\nOutput written under orchestrator_output/_benchmark_{run_id}/ (safe to delete afterward).")

    if args.json_out:
        args.json_out.write_text(json.dumps({
            "job_id": args.job_id, "num_ligands": len(ligands),
            "center": center, "box_size": box, "run_id": run_id,
            "results": rows,
        }, indent=2))
        print(f"Wrote JSON results to {args.json_out}")


if __name__ == "__main__":
    main()
