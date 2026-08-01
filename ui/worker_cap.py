"""Detect the real parallel-docking worker ceiling (no Streamlit dependency).

Kept separate from ui/session_state.py so this logic can be unit tested
directly, matching the same pattern already used by ui/job_history.py and
ui/ollama_models.py.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def detect_moldina_worker_cap(compose_path: Optional[Path] = None) -> int:
    """Best-effort real ceiling for the GUI's "Max parallel workers" control.

    os.cpu_count() here reflects the *ui* container's own CPU visibility,
    which is unrestricted — moldina-runner is a different container with its
    own `deploy.resources.limits.cpus` cgroup quota, the number that actually
    matters (see MoldinaRunner._detect_effective_cpu_count()). Read that
    quota straight out of docker-compose.yml (bind-mounted into this
    container alongside the rest of the repo) instead of guessing from this
    container's own core count, so the GUI doesn't invite worker counts that
    just get silently clamped down server-side anyway. Falls back to
    os.cpu_count() if the compose file can't be found/parsed/read, or no
    limit is configured there — never lets a parsing failure block startup.
    """
    fallback = os.cpu_count() or 1
    if compose_path is None:
        compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"

    try:
        import yaml

        with open(compose_path) as f:
            compose = yaml.safe_load(f)
        cpus = compose["services"]["moldina-runner"]["deploy"]["resources"]["limits"]["cpus"]
        limit = int(float(cpus))
        if limit > 0:
            return limit
    except Exception:
        pass
    return fallback
