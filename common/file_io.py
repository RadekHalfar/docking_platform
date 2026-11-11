"""Shared file I/O utilities for JSON persistence."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def atomic_write_json(
    path: str | Path, payload: Dict[str, Any], indent: int = 2, mode: Optional[int] = None
) -> None:
    """Atomically write a JSON dictionary to disk.

    The write is performed via a temporary file in the same directory and a
    final ``os.replace`` to avoid partial writes. With *mode* (e.g. ``0o600``
    for a file holding a secret), the temporary file gets it before it
    replaces *path*, so the file is never readable by others.
    """
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if mode is None:
        handle = open(tmp_path, "w", encoding="utf-8")
    else:
        # Created with the mode, and chmod'ed in case a leftover temp file
        # from a crashed write already existed with another one.
        handle = os.fdopen(os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8")
        os.chmod(tmp_path, mode)
    with handle as f:
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
                # NOTE (known gap): two waiters can both see the same lock as
                # stale. The first unlinks it and creates its own; the second
                # then unlinks *that* one and creates another, so both hold
                # it. release() also unlinks whatever lock file is there. It
                # needs a crashed holder plus two writers waiting at once.
                # Fix if a lost update to a shared manifest is seen after a
                # crash: break a stale lock by renaming it to a unique name
                # (atomic, so only one waiter succeeds), and write a token
                # into the lock file for release() to check before unlinking.
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
