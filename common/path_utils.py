"""Shared path normalization helpers for host/container workflows."""

from __future__ import annotations

from pathlib import Path


def normalize_path_for_docker(path: str) -> str:
    """Normalize path separators for Docker-friendly JSON payloads."""
    return str(path).replace("\\", "/")


def to_container_path(path: str, workspace_root: Path, container_root: str = "/app") -> str:
    """Convert a host path into container-mounted path when possible.

    - Keeps absolute Windows paths as slash-normalized strings.
    - Maps paths under ``workspace_root`` to ``container_root``.
    - Returns absolute slash-normalized host path for external locations.
    """
    path_str = normalize_path_for_docker(path)

    # Absolute Windows path (e.g. C:/...)
    if len(path_str) >= 3 and path_str[1] == ":" and path_str[2] == "/":
        return path_str

    abs_path = Path(path_str).resolve()

    try:
        rel_path = abs_path.relative_to(Path(workspace_root).resolve())
        return (Path(container_root) / rel_path).as_posix()
    except ValueError:
        return abs_path.as_posix()
