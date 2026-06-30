"""Shared filesystem discovery utilities."""

from __future__ import annotations

from pathlib import Path
from typing import List

LIGAND_EXTENSIONS = {".sdf", ".mol2", ".pdbqt", ".mol"}


def scan_ligand_folder(folder_path: str, recursive: bool = True) -> List[str]:
    """Return a sorted list of ligand file paths found in a folder.

    Matches files whose suffix is in LIGAND_EXTENSIONS (case-insensitive).
    """
    root = Path(folder_path)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {folder_path}")

    pattern = "**/*" if recursive else "*"
    found = [
        str(p) for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in LIGAND_EXTENSIONS
    ]
    return sorted(found)
