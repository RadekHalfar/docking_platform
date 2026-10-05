"""Detect the real parallel-docking worker ceiling (no Streamlit dependency).

Kept separate from ui/session_state.py so this logic can be unit tested
directly, matching the same pattern already used by ui/job_history.py and
ui/ollama_models.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import yaml

_CPUS_KEY_PATH = ("services", "moldina-runner", "deploy", "resources", "limits", "cpus")

# Measured default for "Max parallel workers" (Stage 14.3). Benchmarked
# 2026-10-05 with moldina-runner limits cpus '10' / memory 8G, 50 ligands:
# docs/benchmarks/2026-10-05_moldina_cpus10_mem8g.json, table in
# docs/BATCH_DOCKING.md ("Benchmark baseline"). Rule: the smallest worker
# count within 5% of the fastest wall clock (8 -> 24.3 s vs 10 -> 23.4 s).
# Re-run benchmark_docking.py --sample-stats and update all three numbers after
# changing moldina-runner's resource limits; describe_worker_cap() says so in
# the GUI when the detected cap no longer matches MEASURED_AT_CPUS.
# tests/test_worker_cap.py re-derives the two measurements from the JSON (and
# from its Review Stage R1 re-run, ..._r1_rerun.json, which gave the same).
MEASURED_BEST_WORKERS = 8
# Worst idle-subtracted peak memory per worker seen across all configurations
# (611.5 MiB at 2 workers), rounded up — a sampled lower bound, not a hard limit.
MEASURED_PEAK_MIB_PER_WORKER = 612
# moldina-runner's limits.cpus when the two numbers above were measured.
MEASURED_AT_CPUS = 10

_MEMORY_NOTE = (
    "Running many ligands in parallel increases memory usage — lower this if "
    "docking sessions start failing unexpectedly."
)


@dataclass(frozen=True)
class WorkerCap:
    """The GUI's worker ceiling and where it came from.

    ``reason`` is set only when ``source == "fallback"`` and says why
    docker-compose.yml couldn't supply the limit.
    """

    cap: int
    source: Literal["compose", "fallback"]
    reason: Optional[str] = None


def detect_moldina_worker_cap(compose_path: Optional[Path] = None) -> WorkerCap:
    """Best-effort real ceiling for the GUI's "Max parallel workers" control.

    os.cpu_count() here reflects the *ui* container's own CPU visibility,
    which is unrestricted — moldina-runner is a different container with its
    own `deploy.resources.limits.cpus` cgroup quota, the number that actually
    matters (see MoldinaRunner._detect_effective_cpu_count()). Read that
    quota straight out of docker-compose.yml (bind-mounted into this
    container alongside the rest of the repo) instead of guessing from this
    container's own core count, so the GUI doesn't invite worker counts that
    just get silently clamped down server-side anyway.

    Mirrors the server's own rule: a fractional quota is floored, never
    below 1, and the result is never above this machine's core count (the
    ui and moldina-runner containers share the host, and the server takes
    min(quota, os.cpu_count()) too).

    Falls back to os.cpu_count() if the compose file can't be read or parsed,
    or has no usable limit — never blocks startup. The fallback is returned
    explicitly (``source="fallback"`` plus a ``reason``) rather than
    swallowed, so the GUI can tell the user the number may be too high.

    NOTE (R1, Phase 14 review, docs/reviews/stage_14_r1.md): this reads the
    *configured* limit, a copy of what the server computes. It can only
    drift from the server's real ceiling if moldina-runner stops running on
    this host with this compose file (a remote/HPC runner, a renamed
    service). If that happens, have moldina-runner report its effective
    worker count (MoldinaRunner._detect_effective_cpu_count()) over MCP and
    query it here instead, keeping this file read only as the fallback.
    """
    if compose_path is None:
        compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"

    machine_cpus = os.cpu_count() or 1

    def fallback(reason: str) -> WorkerCap:
        return WorkerCap(machine_cpus, "fallback", reason)

    try:
        with open(compose_path, encoding="utf-8") as f:
            compose = yaml.safe_load(f)
    except FileNotFoundError:
        return fallback(f"docker-compose.yml not found at {compose_path}")
    except OSError as e:
        return fallback(f"couldn't read {compose_path}: {e}")
    except UnicodeDecodeError:
        return fallback(f"{compose_path} is not UTF-8 text")
    except yaml.YAMLError:
        return fallback(f"{compose_path} is not valid YAML")

    try:
        cpus = compose
        for key in _CPUS_KEY_PATH:
            cpus = cpus[key]
    except (KeyError, TypeError):
        return fallback(f"no {'.'.join(_CPUS_KEY_PATH)} in docker-compose.yml")

    try:
        quota = float(cpus)
    except (TypeError, ValueError):
        return fallback(f"limits.cpus value {cpus!r} is not a number")
    if not 0 < quota < float("inf"):  # also rejects nan
        return fallback(f"limits.cpus is {cpus!r}, not a positive number")
    return WorkerCap(min(max(1, int(quota)), machine_cpus), "compose")


def default_worker_count(worker_cap: WorkerCap) -> int:
    """The GUI's default worker count: the measured best, never above the cap."""
    return min(MEASURED_BEST_WORKERS, worker_cap.cap)


def describe_worker_cap(worker_cap: WorkerCap) -> tuple[Optional[str], str]:
    """Return the GUI's (warning, caption) text for *worker_cap*.

    The warning is None unless the cap came from the fallback path. The
    benchmark sentence names the CPU limit it was measured at whenever the
    detected cap differs from it, since the numbers may not hold there.
    """
    if worker_cap.source == "compose" and worker_cap.cap == MEASURED_AT_CPUS:
        conditions = ""
    else:
        conditions = f" at a {MEASURED_AT_CPUS}-CPU limit, which may not hold here"
    measured = (
        f"Benchmarked best{conditions}: {MEASURED_BEST_WORKERS} workers (sampled peak "
        f"~{MEASURED_PEAK_MIB_PER_WORKER} MiB memory each; see docs/BATCH_DOCKING.md)."
    )
    if worker_cap.source == "compose":
        return None, (
            f"Docking service is limited to {worker_cap.cap} CPU{'' if worker_cap.cap == 1 else 's'} "
            f"(docker-compose.yml). {measured} {_MEMORY_NOTE}"
        )
    warning = (
        "Couldn't read the docking service's CPU limit from docker-compose.yml; "
        "showing this machine's core count instead. The real limit may be lower. "
        f"({worker_cap.reason})"
    )
    return warning, f"{measured} {_MEMORY_NOTE}"
