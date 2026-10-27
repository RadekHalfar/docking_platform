"""Find a running Ollama, list its models and download new ones (no GUI
framework dependency)."""
from __future__ import annotations

import socket
import threading
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlsplit

from services.llm_orchestrator.llm_adapters import pull_ollama_model

# Where an Ollama is looked for when the endpoint is still a default one
# (Stage 18.3), in this order:
# - the bundled container of the `local-llm` compose profile;
# - the Ollama app on the host itself (GPU / Apple Metal accelerated), which
#   the ui container reaches through docker-compose.yml's `extra_hosts`;
# - the same app for a GUI run outside Docker.
OLLAMA_CANDIDATES = (
    "http://ollama:11434",
    "http://host.docker.internal:11434",
    "http://localhost:11434",
)
# Short: the dialog waits for every candidate that doesn't answer.
PROBE_TIMEOUT_S = 1.5

PULL_BUSY = "Another model download is already running — wait for it to finish."
# One download per GUI process: parallel pulls only compete for the same
# bandwidth and disk.
_pull_lock = threading.Lock()


def list_ollama_models(endpoint: str, timeout: float = 3.0) -> Optional[List[str]]:
    """Return sorted model names available at an Ollama endpoint's /api/tags.

    Returns None if the endpoint is empty, unreachable or doesn't answer like
    Ollama, and an empty list if it is reachable with no models. *timeout*
    also bounds the host name lookup (see :func:`_resolves`).
    """
    if not endpoint or not _resolves(urlsplit(endpoint).hostname or "", timeout):
        return None

    import requests

    try:
        resp = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    names = {m.get("name") for m in data.get("models", []) if m.get("name")}
    return sorted(names)


def _resolves(host: str, timeout: float) -> bool:
    """Whether *host* resolves within *timeout* seconds.

    requests' timeout doesn't cover the name lookup, and Docker's DNS takes
    about 7 s to say that a name such as ``ollama`` doesn't exist (measured
    in Stage 18.3) -- which is the default stack without the `local-llm`
    profile, so every settings dialog open would wait that long. The lookup
    runs in a daemon thread that is left to finish on its own.
    """
    if not host:
        return False
    answer: list[bool] = []

    def lookup() -> None:
        try:
            socket.getaddrinfo(host, None)
        except (socket.gaierror, UnicodeError):
            answer.append(False)
        else:
            answer.append(True)

    worker = threading.Thread(target=lookup, name=f"resolve {host}", daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(answer and answer[0])


def _normalized(endpoint: str) -> str:
    return endpoint.strip().rstrip("/")


def find_ollama(endpoint: str, default_endpoint: str = "") -> Optional[Tuple[str, List[str]]]:
    """The first Ollama that answers, as ``(endpoint, models)``, or None.

    *endpoint* is asked first. Only when it is a default one (a candidate,
    or *default_endpoint*) are the other :data:`OLLAMA_CANDIDATES` asked
    too: a server the user typed in is reported as unreachable, never
    silently swapped for another one.
    """
    first = _normalized(endpoint)
    defaults = {_normalized(c) for c in OLLAMA_CANDIDATES}
    if default_endpoint:
        defaults.add(_normalized(default_endpoint))
    candidates = [first] if first else []
    if not first or first in defaults:
        candidates += [c for c in OLLAMA_CANDIDATES if _normalized(c) != first]
    for candidate in candidates:
        models = list_ollama_models(candidate, timeout=PROBE_TIMEOUT_S)
        if models is not None:
            return candidate, models
    return None


def pull_model(
    endpoint: str,
    name: str,
    on_progress: Callable[[str, int, int], None],
    should_cancel: Callable[[], bool],
) -> bool:
    """Download *name* into the Ollama at *endpoint*; False if cancelled.

    See :func:`~services.llm_orchestrator.llm_adapters.pull_ollama_model`.
    Raises RuntimeError(:data:`PULL_BUSY`) while another download runs.
    """
    if not _pull_lock.acquire(blocking=False):
        raise RuntimeError(PULL_BUSY)
    try:
        return pull_ollama_model(endpoint, name, on_progress=on_progress, should_cancel=should_cancel)
    finally:
        _pull_lock.release()
