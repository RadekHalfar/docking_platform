"""Shared path normalization helpers for host/container workflows."""

from __future__ import annotations

import platform
from pathlib import Path


def normalize_path_for_docker(path: str) -> str:
    """Normalize path separators for Docker-friendly JSON payloads."""
    return str(path).replace("\\", "/")


def to_container_path(path: str, workspace_root: Path, container_root: str = "/app") -> str:
    """Convert a host path into container-mounted path when possible.

    - Maps paths under ``workspace_root`` to ``container_root`` (this is the
      common case: a Windows or Linux host path under the project's
      bind-mounted ``orchestrator_output``/``demo_output`` trees, sent to a
      Dockerized MCP service as the equivalent ``/app/...`` path).
    - Keeps absolute Windows paths outside ``workspace_root`` as
      slash-normalized strings.
    - Returns absolute slash-normalized host path for other external
      locations.
    """
    path_str = normalize_path_for_docker(path)
    is_windows_drive_path = len(path_str) >= 3 and path_str[1] == ":" and path_str[2] == "/"

    # A Windows-drive-style string ("C:/...") can't be resolved correctly
    # with POSIX pathlib semantics -- if this code is itself running on
    # Linux (e.g. inside a container that received such a string from a
    # Windows caller), Path() would treat it as *relative* and resolve it
    # against the container's own cwd, producing nonsense. Preserve it
    # as-is in that case. On native Windows, resolving it below is correct
    # and necessary -- it's the only way to tell whether the path is
    # actually under workspace_root and should be remapped.
    if is_windows_drive_path and platform.system() != "Windows":
        return path_str

    abs_path = Path(path_str).resolve()

    try:
        rel_path = abs_path.relative_to(Path(workspace_root).resolve())
        return (Path(container_root) / rel_path).as_posix()
    except ValueError:
        return abs_path.as_posix()
