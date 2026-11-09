"""Tests for ui/core/uploads.py -- hermetic, plain GuiSession,
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

    assert dest.name == "receptor.pdb"
    assert dest.parent.parent == upload_dir
    assert dest.read_bytes() == b"ATOM"


@pytest.mark.parametrize("name", ["", ".", "..", "dir/.."])
def test_save_upload_refuses_an_unusable_name(tmp_path, name):
    with pytest.raises(ValueError, match="Unusable uploaded filename"):
        uploads.save_upload(name, b"x", tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_save_upload_creates_an_upload_dir_every_uid_can_write(tmp_path, umask_022):
    # Other uids write there too: the services, and root containers.
    upload_dir = tmp_path / "uploads"
    dest = uploads.save_upload("a.sdf", b"x", upload_dir)
    assert stat.S_IMODE(upload_dir.stat().st_mode) == 0o777
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o777


def test_a_same_named_upload_leaves_the_earlier_file_alone(tmp_path):
    # Another tab (or the other GUI) uploading its own ligand.sdf must not
    # change the molecule an earlier session's parameters point to.
    # It also never writes over another uid's file, the
    # "[Errno 13] Permission denied" found 2026-10-12.
    upload_dir = tmp_path / "uploads"
    first = uploads.save_upload("ligand.sdf", b"first", upload_dir)
    first.chmod(0o444)

    second = uploads.save_upload("ligand.sdf", b"second", upload_dir)

    assert second != first and second.name == first.name == "ligand.sdf"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_saved_upload_is_readable_and_replaceable_by_every_uid(tmp_path, umask_022):
    dest = uploads.save_upload("a.sdf", b"x", tmp_path / "uploads")
    assert stat.S_IMODE(dest.stat().st_mode) == 0o666


def test_failed_upload_write_leaves_nothing_behind(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    kept = uploads.save_upload("a.sdf", b"old", upload_dir)

    def fail(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(uploads.Path, "write_bytes", fail)
    with pytest.raises(OSError, match="disk full"):
        uploads.save_upload("a.sdf", b"new", upload_dir)

    assert list(upload_dir.iterdir()) == [kept.parent]
    assert kept.read_bytes() == b"old"


def test_protein_upload_replaces_the_pdb_id(state, tmp_path):
    state.params["protein_id"] = "6lu7"

    uploads.apply_protein_upload(state, "receptor.pdb", tmp_path / "receptor.pdb")

    assert state.params == {"protein_file": str(tmp_path / "receptor.pdb")}
    assert state.messages[-1]["content"] == "Local protein file set: **receptor.pdb**"


@pytest.mark.parametrize("folder_mode, folder_kept", [(False, True), (True, False)])
def test_ligand_uploads_replace_ligand_ids(state, tmp_path, folder_mode, folder_kept):
    state.params.update(ligand_ids=["ATP"], ligand_folder="/data/ligands")
    paths = [tmp_path / "a.sdf", tmp_path / "b.mol2"]

    uploads.apply_ligand_uploads(state, paths, folder_mode=folder_mode)

    assert state.params["ligand_files"] == [str(p) for p in paths]
    assert "ligand_ids" not in state.params
    assert ("ligand_folder" in state.params) is folder_kept
    assert state.messages[-1]["content"] == "2 local ligand file(s) set: **a.sdf, b.mol2**"


def test_appended_ligand_uploads_add_to_the_list_and_name_them_all(state, tmp_path):
    # The uploader hands over one drop at a time, so a second drop adds.
    uploads.apply_ligand_uploads(state, [tmp_path / "1" / "a.sdf"])
    uploads.apply_ligand_uploads(state, [tmp_path / "2" / "b.mol2"])

    assert state.params["ligand_files"] == [str(tmp_path / "1" / "a.sdf"), str(tmp_path / "2" / "b.mol2")]
    assert state.messages[-1]["content"] == "2 local ligand file(s) set: **a.sdf, b.mol2**"


def test_a_dropped_ligand_named_like_a_listed_one_replaces_it(state, tmp_path):
    # Two files of one name would fail the run's "Local ligand filename
    # collision" check, so the newest replaces it.
    uploads.apply_ligand_uploads(state, [tmp_path / "1" / "a.sdf"])
    uploads.apply_ligand_uploads(state, [tmp_path / "2" / "b.mol2"])
    uploads.apply_ligand_uploads(state, [tmp_path / "3" / "a.sdf"])

    assert state.params["ligand_files"] == [str(tmp_path / "2" / "b.mol2"), str(tmp_path / "3" / "a.sdf")]
    assert state.messages[-1]["content"] == (
        "2 local ligand file(s) set: **b.mol2, a.sdf** (replaced the earlier a.sdf)"
    )


def test_two_files_of_one_name_in_one_drop_keep_only_the_last(state, tmp_path):
    # E.g. set1/a.sdf and set2/a.sdf dropped together, each saved in its
    # own upload dir, must not both stay listed and fail the run's
    # filename-collision check.
    uploads.apply_ligand_uploads(state, [tmp_path / "1" / "a.sdf", tmp_path / "2" / "b.sdf", tmp_path / "3" / "a.sdf"])

    assert state.params["ligand_files"] == [str(tmp_path / "2" / "b.sdf"), str(tmp_path / "3" / "a.sdf")]
    assert state.messages[-1]["content"] == (
        "2 local ligand file(s) set: **b.sdf, a.sdf** (replaced the earlier a.sdf)"
    )


def test_clearing_uploads_drops_only_their_paths(state):
    state.params.update(protein_file="/x/r.pdb", ligand_files=["/x/a.sdf"], center=[1, 2, 3])

    uploads.clear_protein_upload(state)
    uploads.clear_ligand_uploads(state)

    assert state.params == {"center": [1, 2, 3]}
