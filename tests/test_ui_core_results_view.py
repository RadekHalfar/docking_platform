"""Tests for ui/core/results_view.py (Stage 16.2) -- hermetic, tmp_path
OUTPUT_BASE. Result paths use the MCP services' own mount
(``/app/orchestrator_output/...``), as real workflow results do."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ui.core import results_view

JOB_ID = "job_20261008_000003_f00d03"
MOUNT = f"/app/orchestrator_output/{JOB_ID}"


def _sdf(*names):
    return "".join(f"{n}\n  mol\n\n  0  0  0  0  0  0            999 V2000\nM  END\n$$$$\n" for n in names)


@pytest.fixture
def job(tmp_path):
    """Two docked ligands: ATP with three poses (all formats), HEM with one."""
    root = tmp_path / JOB_ID
    (root / "docking_results").mkdir(parents=True)
    (root / "visualization").mkdir()
    (root / "docking_results" / "ATP_out.pdbqt").write_text("pdbqt")
    (root / "docking_results" / "HEM_out.pdbqt").write_text("pdbqt")
    (root / "visualization" / "ATP.sdf").write_text(_sdf("p1", "p2", "p3"))
    (root / "visualization" / "ATP.pdb").write_text("pdb")
    (root / "visualization" / "HEM.sdf").write_text(_sdf("h1"))
    result = {
        "status": "completed",
        "results": {
            "dock": {
                "docking_algorithm": "vina",
                "results": [
                    {"ligand_id": "ATP", "output_path": f"{MOUNT}/docking_results/ATP_out.pdbqt",
                     "best_affinity": -8.0, "modes": [{"affinity": -8.0}, {"affinity": -7.5}]},
                    {"ligand_id": "HEM", "output_path": f"{MOUNT}/docking_results/HEM_out.pdbqt",
                     "best_affinity": -6.0},
                    {"ligand_id": "ADP", "output_path": f"{MOUNT}/docking_results/ADP_out.pdbqt"},
                    {"output_path": "ignored, no ligand_id"},
                ],
            },
            "visualize": {
                "converted": [
                    {"ligand_id": "ATP", "sdf_path": f"{MOUNT}/visualization/ATP.sdf",
                     "pdb_path": f"{MOUNT}/visualization/ATP.pdb"},
                    {"ligand_id": "HEM", "sdf_path": f"{MOUNT}/visualization/HEM.sdf"},
                    {"ligand_id": "ADP", "sdf_path": f"{MOUNT}/visualization/ADP.sdf"},
                    {"ligand_id": "XYZ"},
                ],
            },
        },
    }
    return result


# ---------------------------------------------------------------------------
# Labels and captions
# ---------------------------------------------------------------------------

def test_job_label_bolds_and_escapes_a_name():
    assert results_view.job_label(JOB_ID, None) == f"`{JOB_ID}`"
    assert results_view.job_label(JOB_ID, "a*b") == f"**a\\*b** (`{JOB_ID}`)"


def test_docked_with_caption(job):
    assert results_view.docked_with_caption(job) == "Docked with Vina (Monte Carlo)"
    assert results_view.docked_with_caption({"results": {}}) is None


# ---------------------------------------------------------------------------
# Progress and the job log
# ---------------------------------------------------------------------------

def test_read_docking_progress(tmp_path):
    assert results_view.read_docking_progress(tmp_path, JOB_ID) is None
    path = tmp_path / JOB_ID / "docking_results" / "docking_progress.json"
    path.parent.mkdir(parents=True)
    path.write_text("{half")
    assert results_view.read_docking_progress(tmp_path, JOB_ID) is None
    path.write_text(json.dumps({"status": "running", "total": 4}))
    assert results_view.read_docking_progress(tmp_path, JOB_ID) == {"status": "running", "total": 4}


def test_currently_docking_gives_the_elapsed_seconds():
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    progress = {"in_progress": [
        {"ligand": "ATP", "started_at": (now - timedelta(seconds=42)).isoformat()},
        {"ligand": "HEM"},
        {"ligand": "ADP", "started_at": "not a time"},
        {"started_at": now.isoformat()},
    ]}
    assert results_view.currently_docking(progress, now) == [("ATP", 42), ("HEM", None), ("ADP", None)]
    assert results_view.currently_docking({"in_progress": None}, now) == []


def test_read_job_log(tmp_path):
    assert results_view.read_job_log(tmp_path, JOB_ID) is None
    (tmp_path / JOB_ID).mkdir()
    (tmp_path / JOB_ID / "job.log").write_bytes(b"line 1\nbad \xff byte\n")
    assert results_view.read_job_log(tmp_path, JOB_ID) == "line 1\nbad � byte\n"


# ---------------------------------------------------------------------------
# Poses
# ---------------------------------------------------------------------------

def test_split_sdf_records_keeps_each_record_valid():
    records = results_view.split_sdf_records(_sdf("a", "b") + "\n\n")
    assert len(records) == 2
    assert all(r.endswith("\n$$$$\n") for r in records)
    assert records[1].startswith("b\n")
    assert results_view.split_sdf_records("a\r\nM  END\r\n$$$$\r\n") == ["a\r\nM  END\n$$$$\n"]


def test_dock_results_by_ligand_skips_entries_without_an_id(job):
    assert list(results_view.dock_results_by_ligand(job)) == ["ATP", "HEM", "ADP"]


def test_pose_records_reads_each_ligands_poses_in_order(tmp_path, job):
    records = results_view.pose_records(job, tmp_path)
    assert list(records) == ["ATP", "HEM"]  # ADP's SDF is missing, XYZ has none
    assert [len(r) for r in records.values()] == [3, 1]


def test_pose_records_reads_through_the_given_reader(tmp_path, job):
    read = []
    results_view.pose_records(job, tmp_path, lambda p: read.append(p.name) or p.read_bytes())
    assert read == ["ATP.sdf", "HEM.sdf"]


def test_viewer_ligand_picks_the_pose_and_its_affinity(tmp_path, job):
    records = results_view.pose_records(job, tmp_path)["ATP"]
    entry = results_view.dock_results_by_ligand(job)["ATP"]

    assert results_view.viewer_ligand(records, entry, 2) == {"sdf": records[1], "affinity": -7.5}
    # Pose 3 has no mode entry: the ligand's best affinity is shown.
    assert results_view.viewer_ligand(records, entry, 3) == {"sdf": records[2], "affinity": -8.0}
    # A selection beyond the poses there are is clamped to the last one.
    assert results_view.viewer_ligand(records, entry, 9)["sdf"] == records[2]
    assert results_view.viewer_ligand(records[:1], {}, 1) == {"sdf": records[0], "affinity": None}


def test_pose_label():
    modes = [{"affinity": -8.0}, {"affinity": None}]
    assert results_view.pose_label(modes, 1) == "Mode 1 (-8.00 kcal/mol)"
    assert results_view.pose_label(modes, 2) == "Mode 2"
    assert results_view.pose_label(modes, 3) == "Mode 3"


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def test_pose_download_paths_per_format(tmp_path, job):
    def names(fmt):
        return [(lig, p.name) for lig, p in results_view.pose_download_paths(job, tmp_path, fmt)]

    assert names("pdbqt") == [("ATP", "ATP_out.pdbqt"), ("HEM", "HEM_out.pdbqt")]
    assert names("pdb") == [("ATP", "ATP.pdb")]
    assert names("mol2") == []
    assert results_view.POSE_DOWNLOAD_FORMATS[0] == "pdbqt"


def test_other_download_files_skips_saved_bundles(tmp_path):
    root = tmp_path / JOB_ID
    for rel in ("b.json", "visualization/a.png", "visualization/s.pml", "x.pdbqt",
                "saved_results/run1/c.json", "nested/saved_results/d.json"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")

    files = results_view.other_download_files(tmp_path, JOB_ID)

    assert [f.relative_to(root).as_posix() for f in files] == [
        "b.json", "nested/saved_results/d.json", "visualization/a.png", "visualization/s.pml",
    ]


# ---------------------------------------------------------------------------
# One result's reads (Stage 16.6)
# ---------------------------------------------------------------------------

def test_read_results_data_reads_receptor_poses_and_download_lists(tmp_path, job):
    (tmp_path / JOB_ID / "visualization" / "receptor.pdb").write_text("ATOM      1  N\n")
    (tmp_path / JOB_ID / "visualization" / "s.pml").write_text("load x\n")
    job["results"]["visualize"]["receptor_pdb"] = f"{MOUNT}/visualization/receptor.pdb"
    read = []

    data = results_view.read_results_data(
        tmp_path, JOB_ID, job, lambda p: read.append(p.name) or p.read_bytes()
    )

    assert data.receptor_pdb == "ATOM      1  N\n" and not data.receptor_missing
    assert [len(r) for r in data.pose_records.values()] == [3, 1]
    assert set(data.pose_downloads) == set(results_view.POSE_DOWNLOAD_FORMATS)
    assert [lig for lig, _ in data.pose_downloads["pdb"]] == ["ATP"]
    assert [f.name for f in data.other_files] == ["s.pml"]
    assert sorted(read) == ["ATP.sdf", "HEM.sdf", "receptor.pdb"]  # each file once


def test_read_results_data_without_receptor_or_job_dir(tmp_path, job):
    assert results_view.read_results_data(tmp_path, JOB_ID, job).receptor_missing is False

    job["results"]["visualize"]["receptor_pdb"] = f"{MOUNT}/gone.pdb"
    data = results_view.read_results_data(tmp_path, "job_20261008_000009_absent", job)
    assert data.receptor_pdb is None and data.receptor_missing
    assert data.other_files == []


def test_job_log_if_changed_rereads_only_a_changed_log(tmp_path):
    assert results_view.job_log_if_changed(tmp_path, JOB_ID, None) == (None, None)
    log = tmp_path / JOB_ID / "job.log"
    log.parent.mkdir()
    log.write_text("line 1\n")

    stat, text = results_view.job_log_if_changed(tmp_path, JOB_ID, None)
    assert text == "line 1\n"
    assert results_view.job_log_if_changed(tmp_path, JOB_ID, stat) == (stat, None)

    with log.open("a") as f:
        f.write("line 2\n")
    new_stat, text = results_view.job_log_if_changed(tmp_path, JOB_ID, stat)
    assert new_stat != stat and text == "line 1\nline 2\n"


def test_job_log_tail_keeps_the_last_lines():
    text = "\n".join(f"l{i}" for i in range(results_view.JOB_LOG_TAIL_LINES + 5))
    tail = results_view.job_log_tail(text).splitlines()
    assert len(tail) == results_view.JOB_LOG_TAIL_LINES and tail[0] == "l5"


# ---------------------------------------------------------------------------
# The download route's path checks (Stage 16.6, finding 3)
# ---------------------------------------------------------------------------

@pytest.fixture
def files(tmp_path):
    root = tmp_path / JOB_ID
    (root / "visualization").mkdir(parents=True)
    (root / "visualization" / "a b.png").write_text("png")
    (root / "job.log").write_text("log")
    (root / "saved_results" / "run1").mkdir(parents=True)
    (root / "saved_results" / "run1" / "c.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("outside")
    (tmp_path / "job_20261008_000004_other").mkdir()
    (tmp_path / "job_20261008_000004_other" / "x.json").write_text("{}")
    return tmp_path


def test_resolve_job_file_serves_a_file_in_the_job_dir(files):
    root = (files / JOB_ID).resolve()
    assert results_view.resolve_job_file(files, JOB_ID, "job.log") == root / "job.log"
    assert results_view.resolve_job_file(files, JOB_ID, "visualization/a b.png") == root / "visualization" / "a b.png"


@pytest.mark.parametrize("job_id, rel_path", [
    (JOB_ID, "../secret.txt"),
    (JOB_ID, "visualization/../../secret.txt"),
    (JOB_ID, "../job_20261008_000004_other/x.json"),
    (JOB_ID, "/etc/passwd"),
    (JOB_ID, "saved_results/run1/c.json"),
    (JOB_ID, "visualization/../saved_results/run1/c.json"),
    (JOB_ID, "visualization"),
    (JOB_ID, ""),
    (JOB_ID, "missing.json"),
    ("..", "secret.txt"),
    ("uploads", "x.sdf"),
    ("job_../..", "secret.txt"),
    (JOB_ID, "nul\x00byte"),
    (JOB_ID + "\n", "workflow_results.json"),  # re's "$" allows a trailing newline
])
def test_resolve_job_file_refuses_anything_else(files, job_id, rel_path):
    assert results_view.resolve_job_file(files, job_id, rel_path) is None


def test_resolve_job_file_refuses_a_symlink_pointing_out(files):
    link = files / JOB_ID / "escape.txt"
    try:
        link.symlink_to(files / "secret.txt")
    except OSError:
        pytest.skip("symlinks not available here")
    assert results_view.resolve_job_file(files, JOB_ID, "escape.txt") is None


def test_job_file_url_quotes_the_path_and_refuses_one_outside(files):
    root = files / JOB_ID
    assert results_view.job_file_url(files, JOB_ID, root / "visualization" / "a b.png") == (
        f"/files/{JOB_ID}/visualization/a%20b.png"
    )
    assert results_view.job_file_url(files, JOB_ID, files / "secret.txt") is None
