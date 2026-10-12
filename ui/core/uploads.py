"""Local file uploads: saving them and taking them into the parameters
(Phase 16, Stage 16.4).

Moved from ``ui/app.py``'s ``_save_upload`` and its three upload
callbacks. How a GUI receives the file (Streamlit's ``UploadedFile``,
NiceGUI's ``ui.upload`` event) stays in the GUI; it hands over the name and
the bytes. A GUI that can re-fire its callback for the same files (Streamlit)
passes ``announce=False`` the second time, so the chat reply is posted once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from common.utils import ensure_shared_directory
from ui.core.state import reply


def save_upload(name: str, data: bytes, upload_dir: Path) -> Path:
    """Save an uploaded file under *upload_dir*, using only its basename.

    A GUI's upload endpoint is a plain HTTP POST exposing whatever filename
    the client sends; without stripping directory components a crafted name
    (e.g. "../../etc/x") could write outside *upload_dir*.
    """
    safe_name = Path(name).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"Unusable uploaded filename: {name!r}")

    # Shared: the root Streamlit UI and the non-root NiceGUI UI (Phase 16)
    # both upload into this one directory.
    dest = ensure_shared_directory(Path(upload_dir)) / safe_name
    dest.write_bytes(data)
    return dest


def apply_protein_upload(state, name: str, path: Path, *, announce: bool = True) -> None:
    """Dock against the uploaded protein file instead of a PDB ID."""
    state.params["protein_file"] = str(path)
    state.params.pop("protein_id", None)
    if announce:
        reply(state, f"Local protein file set: **{name}**")


def clear_protein_upload(state) -> None:
    state.params.pop("protein_file", None)


def apply_ligand_uploads(
    state, names: Sequence[str], paths: Sequence[Path], *, folder_mode: bool = False, announce: bool = True
) -> None:
    """Dock the uploaded ligand files (replacing any ligand IDs, and in
    Batch Processing mode a ligand folder)."""
    state.params["ligand_files"] = [str(p) for p in paths]
    state.params.pop("ligand_ids", None)
    if folder_mode:
        state.params.pop("ligand_folder", None)
    if announce:
        reply(state, f"{len(names)} local ligand file(s) set: **{', '.join(names)}**")


def clear_ligand_uploads(state) -> None:
    state.params.pop("ligand_files", None)
