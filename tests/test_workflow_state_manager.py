"""Phase 4 tests: WorkflowStateManager revision history, load, and undo/restore."""

from __future__ import annotations

import pytest

from services.llm_orchestrator.workflow_definition import with_default_parameters
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
    mgr.save_execution_state_checkpoint(STATE_A, step="get_input_files")
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
    assert data["parameters"] == with_default_parameters(PARAMS_A)
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
    assert data["parameters"] == with_default_parameters(PARAMS_A)
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
    assert restored == with_default_parameters(PARAMS_A)


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
    assert restored == with_default_parameters(PARAMS_B)
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


# ---------------------------------------------------------------------------
# Edge cases — fresh manager behaviour (C2)
# ---------------------------------------------------------------------------

def test_get_current_revision_zero_on_fresh_manager(mgr):
    """A brand-new manager with no checkpoints must report revision 0."""
    assert mgr.get_current_revision() == 0


def test_load_latest_parameters_returns_none_when_no_checkpoint(mgr):
    """load_latest_parameters() on a fresh manager must return None, not raise."""
    assert mgr.load_latest_parameters() is None


def test_load_latest_state_returns_none_when_no_checkpoint(mgr):
    """load_latest_state() on a fresh manager must return None, not raise."""
    assert mgr.load_latest_state() is None


# ---------------------------------------------------------------------------
# Concurrent writers -- _next_revision race condition
# ---------------------------------------------------------------------------

def test_next_revision_is_safe_under_concurrent_writers(mgr):
    """Two writers racing to save a checkpoint at the same time must not
    clobber each other's revision bump. Before the FileLock fix, both could
    read current_revision=N, both compute N+1, and the second writer's
    manifest write would silently drop the first writer's event (though its
    checkpoint file would still exist on disk, permanently orphaned)."""
    import threading

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    revisions: list[int] = []
    revisions_lock = threading.Lock()

    def _save(i: int) -> None:
        barrier.wait()  # maximize actual overlap of the read-modify-write window
        rev = mgr.save_parameters_checkpoint({**PARAMS_A, "worker": i}, source=f"worker_{i}")
        with revisions_lock:
            revisions.append(rev)

    threads = [threading.Thread(target=_save, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Every revision number handed out must be unique (no two writers got
    # the same "next" revision) and dense from 1..n_threads (no gaps from a
    # lost update), and the manifest's own counter must agree.
    assert sorted(revisions) == list(range(1, n_threads + 1))
    assert mgr.get_current_revision() == n_threads
    assert len(mgr.list_revisions()) == n_threads

    # No orphaned checkpoint files: every revision 1..n_threads has one.
    available = mgr._available_revisions()
    assert available == list(range(1, n_threads + 1))


# ---------------------------------------------------------------------------
# Draft parameters -- unconfirmed edits, separate from the revisioned
# checkpoint/audit system
# ---------------------------------------------------------------------------

def test_load_draft_parameters_returns_none_when_none_saved(mgr):
    assert mgr.load_draft_parameters() is None


def test_save_and_load_draft_parameters_round_trips(mgr):
    mgr.save_draft_parameters(PARAMS_A)
    assert mgr.load_draft_parameters() == with_default_parameters(PARAMS_A)


def test_save_draft_parameters_overwrites_in_place(mgr):
    mgr.save_draft_parameters(PARAMS_A)
    mgr.save_draft_parameters(PARAMS_B)
    assert mgr.load_draft_parameters() == with_default_parameters(PARAMS_B)


def test_clear_draft_parameters_removes_it(mgr):
    mgr.save_draft_parameters(PARAMS_A)
    mgr.clear_draft_parameters()
    assert mgr.load_draft_parameters() is None


def test_clear_draft_parameters_is_a_noop_when_nothing_to_clear(mgr):
    mgr.clear_draft_parameters()  # must not raise
    assert mgr.load_draft_parameters() is None


def test_draft_parameters_do_not_bump_revision_or_appear_in_history(mgr):
    """The draft must stay outside the audit/revision system entirely --
    that's what makes it safe to write on every casual edit without either
    spamming job.log or corrupting the redock diff baseline
    (load_latest_parameters(), which only ever reflects real checkpoints)."""
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    before_revision = mgr.get_current_revision()
    before_history_len = len(mgr.list_revisions())

    mgr.save_draft_parameters(PARAMS_B)

    assert mgr.get_current_revision() == before_revision
    assert len(mgr.list_revisions()) == before_history_len
    # The real checkpoint (used for the redock diff) is untouched by the draft.
    assert mgr.load_latest_parameters() == with_default_parameters(PARAMS_A)
    assert mgr.load_draft_parameters() == with_default_parameters(PARAMS_B)


# ---------------------------------------------------------------------------
# job.log (Stage 11.1)
# ---------------------------------------------------------------------------

def _job_log_text(mgr) -> str:
    log_path = mgr.job_dir / "job.log"
    assert log_path.exists(), "job.log should have been created"
    return log_path.read_text(encoding="utf-8")


def test_save_parameters_checkpoint_logs_a_diff_line(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    text = _job_log_text(mgr)
    lines = [line for line in text.splitlines() if "source=interactive_update" in line]
    assert len(lines) == 1, f"expected exactly one diff line for the second save, got: {lines}"

    diff_line = lines[0]
    assert "source=interactive_update" in diff_line
    # Only the keys that actually changed between PARAMS_A and PARAMS_B.
    assert "ligand_ids: ['ATP'] -> ['HEM']" in diff_line
    assert "center: [1.0, 2.0, 3.0] -> [5.0, 6.0, 7.0]" in diff_line
    assert "protein_id" not in diff_line
    assert "box_size" not in diff_line


def test_save_parameters_checkpoint_first_call_logs_all_keys_as_new(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")

    text = _job_log_text(mgr)
    assert "source=initial_parse" in text
    assert "protein_id: None -> '6lu7'" in text


def test_save_execution_state_checkpoint_logs_a_line(mgr):
    mgr.save_execution_state_checkpoint(STATE_A, step="get_input_files")

    text = _job_log_text(mgr)
    assert "State checkpoint saved (step=get_input_files)" in text


def test_restore_revision_logs_a_line(mgr):
    r1 = mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_parameters_checkpoint(PARAMS_B, source="interactive_update")

    mgr.restore_revision(r1)

    text = _job_log_text(mgr)
    assert f"Restored revision {r1}" in text


def test_job_log_lines_are_timestamped_and_ordered(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_execution_state_checkpoint(STATE_A, step="get_input_files")

    lines = [line for line in _job_log_text(mgr).splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("20"), f"expected an ISO timestamp prefix, got: {line}"
    assert "Parameters updated" in lines[0] or "Parameters checkpoint saved" in lines[0]
    assert "State checkpoint saved" in lines[1]


# ---------------------------------------------------------------------------
# get_parameters_revision (Phase 13, Stage 13.2)
# ---------------------------------------------------------------------------

def test_get_parameters_revision_none_before_any_checkpoint(mgr):
    assert mgr.get_parameters_revision() is None


def test_get_parameters_revision_ignores_later_state_updates(mgr):
    rev = mgr.save_parameters_checkpoint(PARAMS_A, source="test")
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    assert mgr.get_parameters_revision() == rev
    assert mgr.get_current_revision() == rev + 1


def test_get_parameters_revision_follows_a_restore(mgr):
    rev_a = mgr.save_parameters_checkpoint(PARAMS_A, source="test")
    mgr.save_parameters_checkpoint(PARAMS_B, source="test")
    mgr.restore_revision(rev_a)
    assert mgr.get_parameters_revision() == mgr.get_current_revision()


def test_parameters_ran_true_without_any_parameter_checkpoint(mgr):
    assert mgr.parameters_ran() is True


def test_parameters_ran_false_until_a_step_runs(mgr):
    mgr.save_parameters_checkpoint(PARAMS_A, source="test")
    assert mgr.parameters_ran() is False
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    assert mgr.parameters_ran() is True


def test_parameters_ran_false_after_a_restore_and_survives_a_new_manager(mgr):
    # The real case that motivated it (Stage 13.3): results from an earlier
    # run, then a restore never redocked -- still detected after a Load,
    # which builds a fresh manager over the same job_dir.
    rev_a = mgr.save_parameters_checkpoint(PARAMS_A, source="test")
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    mgr.save_parameters_checkpoint(PARAMS_B, source="test")
    mgr.save_execution_state_checkpoint(STATE_A, step="dock")
    mgr.restore_revision(rev_a)

    assert mgr.parameters_ran() is False
    assert WorkflowStateManager(mgr.job_dir).parameters_ran() is False


# ---------------------------------------------------------------------------
# Review Stage R1, Phase 15: parameters stored and read with defaults filled
# ---------------------------------------------------------------------------

def _write_pre_phase15_checkpoint(mgr):
    """What an old session has on disk: no docking_algorithm key."""
    import json
    payload = {"revision": 1, "timestamp": "2026-09-01T00:00:00+00:00", "source": "initial_parse",
               "parameters": dict(PARAMS_A)}
    mgr.parameters_file.write_text(json.dumps(payload))
    mgr._checkpoint_path(1, "parameters").write_text(json.dumps(payload))


def test_old_checkpoint_reads_with_the_defaults_it_ran_with(mgr):
    _write_pre_phase15_checkpoint(mgr)
    assert mgr.load_latest_parameters()["docking_algorithm"] == "moldina"
    assert mgr.load_checkpoint(1)["parameters"]["docking_algorithm"] == "moldina"


def test_restating_a_default_on_an_old_checkpoint_logs_no_change(mgr):
    """job.log said "docking_algorithm: None -> 'moldina'" on the first save
    of an old session, although nothing changed."""
    _write_pre_phase15_checkpoint(mgr)
    mgr.save_parameters_checkpoint({**PARAMS_A, "docking_algorithm": "moldina"}, source="interactive_update")
    log = (mgr.job_dir / "job.log").read_text()
    assert "no changes" in log
    assert "None ->" not in log


def test_saved_parameters_are_stored_full(mgr):
    import json
    mgr.save_parameters_checkpoint(PARAMS_A, source="initial_parse")
    mgr.save_draft_parameters(PARAMS_B)
    assert json.loads(mgr.parameters_file.read_text())["parameters"] == with_default_parameters(PARAMS_A)
    assert json.loads(mgr.draft_parameters_file.read_text())["parameters"] == with_default_parameters(PARAMS_B)
