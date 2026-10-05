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

    Falls back to os.cpu_count() if the compose file can't be read or parsed,
    or has no usable limit — never blocks startup. The fallback is returned
    explicitly (``source="fallback"`` plus a ``reason``) rather than
    swallowed, so the GUI can tell the user the number may be too high.
    """
    if compose_path is None:
        compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"

    def fallback(reason: str) -> WorkerCap:
        return WorkerCap(os.cpu_count() or 1, "fallback", reason)

    try:
        with open(compose_path) as f:
            compose = yaml.safe_load(f)
    except FileNotFoundError:
        return fallback(f"docker-compose.yml not found at {compose_path}")
    except OSError as e:
        return fallback(f"couldn't read {compose_path}: {e}")
    except yaml.YAMLError:
        return fallback(f"{compose_path} is not valid YAML")

    try:
        cpus = compose
        for key in _CPUS_KEY_PATH:
            cpus = cpus[key]
    except (KeyError, TypeError):
        return fallback(f"no {'.'.join(_CPUS_KEY_PATH)} in docker-compose.yml")

    try:
        limit = int(float(cpus))
    except (TypeError, ValueError):
        return fallback(f"limits.cpus value {cpus!r} is not a number")
    if limit <= 0:
        return fallback(f"limits.cpus is {cpus!r}, not a positive number")
    return WorkerCap(limit, "compose")


def describe_worker_cap(worker_cap: WorkerCap) -> tuple[Optional[str], str]:
    """Return the GUI's (warning, caption) text for *worker_cap*.

    The warning is None unless the cap came from the fallback path.
    """
    if worker_cap.source == "compose":
        return None, (
            f"Docking service is limited to {worker_cap.cap} CPUs "
            f"(docker-compose.yml). {_MEMORY_NOTE}"
        )
    warning = (
        "Couldn't read the docking service's CPU limit from docker-compose.yml; "
        "showing this machine's core count instead. The real limit may be lower. "
        f"({worker_cap.reason})"
    )
    return warning, _MEMORY_NOTE
