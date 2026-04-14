"""Shared file I/O utilities for JSON persistence."""

from __future__ import annotations

import json
import os
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
