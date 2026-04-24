"""Phase 4 tests: WorkflowStateManager revision history, load, and undo/restore."""

from __future__ import annotations

import pytest

from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PARAMS_A = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1.0, 2.0, 3.0], "box_size": [25.0, 25.0, 25.0]}
PARAMS_B = {**PARAMS_A, "ligand_ids": ["HEM"], "center": [5.0, 6.0, 7.0]}
STATE_A = {"protein_file": "/app/prepared/6lu7.pdbqt", "ligand_files": ["/app/prepared/ATP.pdbqt"]}
STATE_B = {"protein_file": "/app/prepared/6lu7.pdbqt", "docking_results": {"successful": 1}}


@pytest.fixture
def mgr(tmp_path):
    return WorkflowStateManager(tmp_path / "job_test")


# ---------------------------------------------------------------------------
# list_revisions
# ---------------------------------------------------------------------------

def test_list_revisions_empty_initially(mgr):
    assert mgr.list_revisions() == []


def test_list_revisions_is_monotonic(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_execution_state_checkpoint(STATE_A, step="download")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    revisions = mgr.list_revisions()
    nums = [r["revision"] for r in revisions]

    assert nums == sorted(nums), "Revisions must be in ascending order"
    assert nums == [1, 2, 3]


def test_list_revisions_event_types(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")

    revisions = mgr.list_revisions()
    types = {r["revision"]: r["event_type"] for r in revisions}

    assert types[1] == "parameters_update"
    assert types[2] == "state_update"


def test_list_revisions_has_parameters_flag(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    revisions = mgr.list_revisions()

    assert revisions[0]["has_parameters"] is True
    assert revisions[0]["has_state"] is False


def test_list_revisions_has_state_flag(mgr):
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    revisions = mgr.list_revisions()

    assert revisions[0]["has_state"] is True
    assert revisions[0]["has_parameters"] is False


# ---------------------------------------------------------------------------
# load_checkpoint
# ---------------------------------------------------------------------------

def test_load_checkpoint_parameters(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")

    data = mgr.load_checkpoint(1)

    assert data["revision"] == 1
    assert data["parameters"] == PARAMS_A
    assert data["runtime_state"] is None


def test_load_checkpoint_state(mgr):
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")

    data = mgr.load_checkpoint(1)

    assert data["revision"] == 1
    assert data["parameters"] is None
    assert data["runtime_state"] == STATE_A


def test_load_checkpoint_both(mgr):
    """When a revision has BOTH param and state files, both are returned."""
    # Force them onto the same revision number by writing files directly.
    import json
    rev = 1
    mgr.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    (mgr.checkpoints_dir / f"rev_{rev:06d}_parameters.json").write_text(
        json.dumps({"parameters": PARAMS_A})
    )
    (mgr.checkpoints_dir / f"rev_{rev:06d}_state.json").write_text(
        json.dumps({"state": STATE_A})
    )

    data = mgr.load_checkpoint(rev)
    assert data["parameters"] == PARAMS_A
    assert data["runtime_state"] == STATE_A


def test_load_checkpoint_invalid_revision_raises(mgr):
    with pytest.raises(ValueError, match="No checkpoint files found for revision 99"):
        mgr.load_checkpoint(99)


# ---------------------------------------------------------------------------
# restore_revision
# ---------------------------------------------------------------------------

def test_restore_revision_updates_latest_parameters(mgr):
    r1 = mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    # Latest is PARAMS_B; restore to PARAMS_A.
    mgr.restore_revision(r1)

    restored = mgr.load_latest_parameters()
    assert restored == PARAMS_A


def test_restore_revision_records_undo_event(mgr):
    r1 = mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    mgr.restore_revision(r1)

    revisions = mgr.list_revisions()
    undo_events = [r for r in revisions if r["event_type"] == "undo_restore"]
    assert len(undo_events) == 1
    assert undo_events[0]["details"]["restored_from_revision"] == r1


def test_restore_revision_increments_current_revision(mgr):
    r1 = mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    before = mgr.get_current_revision()

    mgr.restore_revision(r1)

    assert mgr.get_current_revision() == before + 1


def test_restore_revision_also_restores_state_when_available(mgr):
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    r1 = mgr.get_current_revision()

    mgr.save_execution_state_checkpoint(STATE_B, step="visualize")

    mgr.restore_revision(r1)

    restored_state = mgr.load_latest_state()
    assert restored_state == STATE_A


def test_restore_revision_invalid_revision_raises(mgr):
    with pytest.raises(ValueError):
        mgr.restore_revision(999)


def test_restore_revision_does_not_resurrect_stale_values(mgr):
    """Only the restored revision's own checkpoint fields are applied; nothing extra."""
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    r1 = mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    # Restore to revision with PARAMS_B values.
    mgr.restore_revision(r1)

    restored = mgr.load_latest_parameters()
    # Must be PARAMS_B exactly, not a merge of A and B.
    assert restored == PARAMS_B
    assert restored["ligand_ids"] == ["HEM"]


# ---------------------------------------------------------------------------
# _available_revisions helper
# ---------------------------------------------------------------------------

def test_available_revisions_returns_sorted(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    available = mgr._available_revisions()
    assert available == sorted(available)
    assert len(available) == 3
