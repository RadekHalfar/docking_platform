"""Shared file I/O utilities for JSON persistence."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def atomic_write_json(path: str | Path, payload: Dict[str, Any], indent: int = 2) -> None:
    """Atomically write a JSON dictionary to disk.

    The write is performed via a temporary file in the same directory and a
    final ``os.replace`` to avoid partial writes.
    """
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent)
    os.replace(tmp_path, output_path)


def load_json_dict(path: str | Path) -> Optional[Dict[str, Any]]:
    """Load a JSON file expected to contain a dictionary.

    Returns ``None`` when the file does not exist, cannot be decoded, or does
    not contain a JSON object.
    """
    input_path = Path(path)
    if not input_path.exists():
        return None

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(data, dict):
        return data
    return None


class FileLock:
    """Cross-platform advisory lock via exclusive file creation.

    Serializes a read-modify-write sequence (e.g. "read a JSON manifest,
    bump a counter, write it back") across threads *and* separate processes
    that all lock the same path, without depending on fcntl (POSIX-only) or
    msvcrt (Windows-only). Not a kernel-enforced lock -- it only protects
    callers that go through this class for the same lock_path.

    A lock file left behind by a process that crashed while holding it would
    otherwise wedge every future run forever; stale_after bounds how long a
    lock can be held before a new acquirer is allowed to break it.
    """

    def __init__(self, lock_path: str | Path, timeout: float = 10.0, stale_after: float = 30.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return
            except FileExistsError:
                pass

            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except OSError:
                age = None  # lock file vanished between the open() failing and this stat()

            if age is not None and age > self.stale_after:
                try:
                    self.lock_path.unlink()
                except OSError:
                    pass
                continue

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Could not acquire lock {self.lock_path} within {self.timeout}s")
            time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
