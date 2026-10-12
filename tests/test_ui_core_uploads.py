"""Tests for ui/core/uploads.py (Stage 16.4) -- hermetic, plain GuiSession,
uploads written under tmp_path."""
import os
import stat

import pytest

from ui.core import uploads
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap


@pytest.fixture
def state():
    s = GuiSession(worker_cap=WorkerCap(10, "compose"))
    s.messages = []
    return s


def test_save_upload_keeps_only_the_basename(tmp_path):
    upload_dir = tmp_path / "uploads"

    dest = uploads.save_upload("../../etc/receptor.pdb", b"ATOM", upload_dir)

    assert dest == upload_dir / "receptor.pdb"
    assert dest.read_bytes() == b"ATOM"


@pytest.mark.parametrize("name", ["", ".", "..", "dir/.."])
def test_save_upload_refuses_an_unusable_name(tmp_path, name):
    with pytest.raises(ValueError, match="Unusable uploaded filename"):
        uploads.save_upload(name, b"x", tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_save_upload_creates_an_upload_dir_every_uid_can_write(tmp_path, umask_022):
    # The root Streamlit UI and the uid-1000 NiceGUI UI share it (Stage 16.3).
    upload_dir = tmp_path / "uploads"
    uploads.save_upload("a.sdf", b"x", upload_dir)
    assert stat.S_IMODE(upload_dir.stat().st_mode) == 0o777


def test_save_upload_replaces_an_existing_file_rather_than_writing_into_it(tmp_path):
    # A same-named upload owned by the other GUI's user (root Streamlit vs
    # uid-1000 NiceGUI) can't be written in place: "[Errno 13] Permission
    # denied" (found 2026-10-12). Replacing it needs only the directory.
    upload_dir = tmp_path / "uploads"
    old = uploads.save_upload("crystal_ligand.mol2", b"old", upload_dir)
    old_inode = old.stat().st_ino
    old.chmod(0o444)

    dest = uploads.save_upload("crystal_ligand.mol2", b"new", upload_dir)

    assert dest.read_bytes() == b"new"
    assert dest.stat().st_ino != old_inode
    assert sorted(p.name for p in upload_dir.iterdir()) == ["crystal_ligand.mol2"]  # no temp file left


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_saved_upload_is_readable_and_replaceable_by_every_uid(tmp_path, umask_022):
    dest = uploads.save_upload("a.sdf", b"x", tmp_path / "uploads")
    assert stat.S_IMODE(dest.stat().st_mode) == 0o666


def test_failed_upload_write_leaves_no_temp_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    uploads.save_upload("a.sdf", b"old", upload_dir)

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(uploads.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        uploads.save_upload("a.sdf", b"new", upload_dir)

    assert sorted(p.name for p in upload_dir.iterdir()) == ["a.sdf"]
    assert (upload_dir / "a.sdf").read_bytes() == b"old"


def test_protein_upload_replaces_the_pdb_id(state, tmp_path):
    state.params["protein_id"] = "6lu7"

    uploads.apply_protein_upload(state, "receptor.pdb", tmp_path / "receptor.pdb")

    assert state.params == {"protein_file": str(tmp_path / "receptor.pdb")}
    assert state.messages[-1]["content"] == "Local protein file set: **receptor.pdb**"


@pytest.mark.parametrize("folder_mode, folder_kept", [(False, True), (True, False)])
def test_ligand_uploads_replace_ligand_ids(state, tmp_path, folder_mode, folder_kept):
    state.params.update(ligand_ids=["ATP"], ligand_folder="/data/ligands")
    paths = [tmp_path / "a.sdf", tmp_path / "b.mol2"]

    uploads.apply_ligand_uploads(state, ["a.sdf", "b.mol2"], paths, folder_mode=folder_mode)

    assert state.params["ligand_files"] == [str(p) for p in paths]
    assert "ligand_ids" not in state.params
    assert ("ligand_folder" in state.params) is folder_kept
    assert state.messages[-1]["content"] == "2 local ligand file(s) set: **a.sdf, b.mol2**"


def test_an_upload_seen_again_is_not_announced_again(state, tmp_path):
    uploads.apply_protein_upload(state, "r.pdb", tmp_path / "r.pdb", announce=False)
    uploads.apply_ligand_uploads(state, ["a.sdf"], [tmp_path / "a.sdf"], announce=False)
    assert state.messages == []
    assert set(state.params) == {"protein_file", "ligand_files"}


def test_clearing_uploads_drops_only_their_paths(state):
    state.params.update(protein_file="/x/r.pdb", ligand_files=["/x/a.sdf"], center=[1, 2, 3])

    uploads.clear_protein_upload(state)
    uploads.clear_ligand_uploads(state)

    assert state.params == {"center": [1, 2, 3]}
