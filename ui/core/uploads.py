"""Local file uploads: saving them and taking them into the parameters
(Phase 16, Stage 16.4).

How the GUI receives the file (NiceGUI's ``ui.upload`` event) stays in the
GUI; it hands over the name and the bytes.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePath
from typing import Sequence

from common.utils import ensure_shared_directory
from ui.core.state import reply


def save_upload(name: str, data: bytes, upload_dir: Path) -> Path:
    """Save an uploaded file as ``<upload_dir>/<new dir>/<basename>``.

    A GUI's upload endpoint is a plain HTTP POST exposing whatever filename
    the client sends; without stripping directory components a crafted name
    (e.g. "../../etc/x") could write outside *upload_dir*.

    Each upload gets a directory of its own, so a file is never replaced
    once a session's parameters point to it: with one flat directory, a
    same-named upload from another tab or the other GUI silently swapped
    the molecule an earlier session docks -- and redocks (Review Stage R2).
    """
    safe_name = Path(name).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"Unusable uploaded filename: {name!r}")

    # Shared: the services (another container) read the files, and older
    # uploads here were written by the former root Streamlit UI.
    upload_dir = ensure_shared_directory(Path(upload_dir))
    dest_dir = ensure_shared_directory(tempfile.mkdtemp(dir=upload_dir, prefix="upload_"))
    dest = dest_dir / safe_name
    try:
        dest.write_bytes(data)
        os.chmod(dest, 0o666)
    except BaseException:
        # No half-written upload left behind; the write's error is raised.
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return dest


def apply_protein_upload(state, name: str, path: Path) -> None:
    """Dock against the uploaded protein file instead of a PDB ID."""
    state.params["protein_file"] = str(path)
    state.params.pop("protein_id", None)
    reply(state, f"Local protein file set: **{name}**")


def clear_protein_upload(state) -> None:
    state.params.pop("protein_file", None)


def apply_ligand_uploads(state, paths: Sequence[Path], *, folder_mode: bool = False) -> None:
    """Add one drop of uploaded ligand files to the ones already set (the
    Clear button empties them), replacing any ligand IDs, and in Batch
    Processing mode a ligand folder. The reply names them all.

    A dropped file named like one already set replaces it (owner's choice,
    Review Stage R3): two files of one name would fail the run's "Local
    ligand filename collision" check."""
    dropped = [str(p) for p in paths]
    dropped_names = {PurePath(f).name for f in dropped}
    kept: list[str] = []
    replaced: list[str] = []
    for f in state.params.get("ligand_files") or []:
        (replaced if PurePath(f).name in dropped_names else kept).append(f)
    files = [*kept, *dropped]
    state.params["ligand_files"] = files
    state.params.pop("ligand_ids", None)
    if folder_mode:
        state.params.pop("ligand_folder", None)
    names = [PurePath(f).name for f in files]
    message = f"{len(names)} local ligand file(s) set: **{', '.join(names)}**"
    if replaced:
        message += f" (replaced the earlier {', '.join(PurePath(f).name for f in replaced)})"
    reply(state, message)


def clear_ligand_uploads(state) -> None:
    state.params.pop("ligand_files", None)
