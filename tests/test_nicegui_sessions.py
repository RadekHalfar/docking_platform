"""The NiceGUI Sessions tab (Phase 16, Stage 16.7): Session History and
Failed Sessions (Load, Rename, Delete, Compare) and the active session's
revision history with Restore.

Ports the sessions rows of the parity table (tests/test_nicegui_parity.py)
from tests/test_ui_apptest.py; each ported test keeps its AppTest name.
Runs in the ui-nice tier only (see tests/test_nicegui_setup.py).
"""
import asyncio
import json
import threading

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing import User  # noqa: E402

from services.llm_orchestrator.workflow_definition import CORE_PARAMS, WORKFLOW_STEPS  # noqa: E402
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager  # noqa: E402
from ui.core import save_results, sessions as core_sessions  # noqa: E402
from ui.core.state import GREETING_MESSAGE  # noqa: E402
from ui_nice import sessions_tab  # noqa: E402
from ui_nice.controller import SAVING  # noqa: E402
from tests.nicegui_harness import (  # noqa: E402
    HISTORY_JOB_ID,
    REV1_PARAMS,
    REV2_PARAMS,
    active_session_with_two_revisions,
    click,
    completed_job_data,
    completed_job_on_disk,
    completed_session_with_artifacts,
    element,
    eventually,
    on_event_loop,
    open_sessions,
    results_loaded,
    send_chat,
    sessions_listed,
    set_value,
    validated,
    viewer,
)
from tests.ui_fakes import FIXTURE_PDB, FakeBridge, RunningBridge  # noqa: E402

JOB_A = "job_20260930_000001_aaaaaa"
JOB_B = "job_20260930_000002_bbbbbb"
FAILED_JOB = "job_20260930_000003_cccccc"


def _write_job(output_base, job_id, data):
    job_dir = output_base / job_id
    job_dir.mkdir()
    (job_dir / "workflow_results.json").write_text(json.dumps(data))
    return job_dir


def _row_text(user, job_id) -> str:
    row = element(user, f"session-row-{job_id}")
    return "\n".join(e.content for e in row.descendants() if isinstance(e, ui.markdown))


def _rows(page) -> list[str]:
    return list(page.sessions._rows)


async def _open(user, page):
    await user.open("/")
    open_sessions(user, page)
    await sessions_listed(page)


async def _restore(user, page, revision):
    element(user, "revision-select").value = revision
    click(user, "restore-revision")
    await sessions_listed(page)


async def _load(user, page, job_id=HISTORY_JOB_ID):
    open_sessions(user, page)
    await sessions_listed(page)
    click(user, f"load-{job_id}")


def _state_manager(page):
    return page.state.interactive_session.state_manager


def _form_at_checkpoint(state):
    # The fixture's checkpoint holds the validator's max_workers default; a
    # real GUI run checkpoints the form's own value, so match it here.
    state.form["param_max_workers"] = validated(REV2_PARAMS)["max_workers"]


# ---------------------------------------------------------------------------
# Session History / Failed Sessions (ported)
# ---------------------------------------------------------------------------

async def test_sidebar_lists_completed_and_failed_sessions(user: User, page, output_base):
    _write_job(output_base, JOB_B, completed_job_data(FIXTURE_PDB))
    _write_job(output_base, FAILED_JOB, {"status": "failed", "job_name": "", "errors": ["no ligands"], "results": {}})

    await _open(user, page)

    completed = _row_text(user, JOB_B)
    assert "trial" in completed and JOB_B in completed and "(1 ligand(s))" in completed
    failed = _row_text(user, FAILED_JOB)
    assert FAILED_JOB in failed and "no ligands" in failed
    assert element(user, f"load-{JOB_B}").enabled and element(user, f"load-{FAILED_JOB}").enabled
    # Only completed sessions can be compared.
    element(user, f"select-{JOB_B}")
    with pytest.raises(AssertionError):
        element(user, f"select-{FAILED_JOB}")


async def test_every_session_is_listed_not_only_the_newest_ten(user: User, page, output_base):
    # Streamlit lists the 10 newest; the Sessions tab lists all (owner, 2026-10-13).
    job_ids = [f"job_20260930_0000{i:02d}_aaaaaa" for i in range(12)]
    for job_id in job_ids:
        _write_job(output_base, job_id, completed_job_data(FIXTURE_PDB))

    await _open(user, page)

    assert _rows(page) == sorted(job_ids, reverse=True)


async def test_empty_lists_say_so(user: User, page):
    await _open(user, page)

    await user.should_see(sessions_tab.NO_COMPLETED)
    await user.should_see(sessions_tab.NO_FAILED)


# ---------------------------------------------------------------------------
# Revision history + Restore (ported)
# ---------------------------------------------------------------------------

async def test_revision_history_expander_lists_revisions(user: User, page, output_base):
    page.seed(lambda state: active_session_with_two_revisions(state, output_base))

    await _open(user, page)

    table = element(user, "revision-table").content
    assert "| 1 |" in table and "| 2 |" in table
    assert "initial\\_parse" in table
    assert "currently at revision 2" in element(user, "revision-current").text
    assert element(user, "revision-select").options == [2, 1]
    assert element(user, "revision-select").value == 2


async def test_revision_history_hidden_without_active_session(user: User, page):
    await _open(user, page)

    assert not element(user, "active-session").visible


async def test_restore_revision_loads_its_params_and_marks_all_pending(user: User, page, output_base):
    def seed(state):
        state_manager = active_session_with_two_revisions(state, output_base)
        state.box_picker_receptor_pdb = str(FIXTURE_PDB)  # same protein: kept
        state_manager.save_draft_parameters({**REV2_PARAMS, "center": [9.0, 9.0, 9.0]})

    page.seed(seed)
    await _open(user, page)
    state_manager = _state_manager(page)

    await _restore(user, page, 1)

    state = page.state
    assert state.form["param_center_x"] == "1.0"
    assert element(user, "param_center_x").value == "1.0"
    assert state.params["center"] == [1.0, 2.0, 3.0]
    # The picker's viewer draws the restored box too.
    assert (await viewer(user)).center == [1.0, 2.0, 3.0]
    # A draft would otherwise win over the restore on the next Load.
    assert state_manager.load_draft_parameters() is None
    assert state_manager.load_latest_parameters()["center"] == [1.0, 2.0, 3.0]
    assert state_manager.get_parameters_revision() == 4
    assert state.interactive_session.pending_changed_params == set(CORE_PARAMS)
    assert "Restored revision 1" in (output_base / HISTORY_JOB_ID / "job.log").read_text()
    assert page.chat_texts()[-1].startswith("Restored revision 1.")
    # The panel re-reads its history after the restore.
    assert "currently at revision 4" in element(user, "revision-current").text


async def test_redock_after_restore_plans_full_rerun(user: User, page, output_base):
    # Without pending_changed_params the form equals the (restored)
    # checkpoint and redock would be a no-op over the newer results.
    page.seed(lambda state: active_session_with_two_revisions(state, output_base))
    await _open(user, page)
    await _restore(user, page, 1)

    click(user, "run-docking")

    assert page.parts[-1].controller.error is None
    assert set(page.state.pending_rerun["steps"]) == set(WORKFLOW_STEPS)
    # Nothing differed from the restored checkpoint -> no extra revision.
    assert _state_manager(page).get_parameters_revision() == 4


async def test_restore_disabled_while_job_runs(user: User, page, output_base):
    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.bridge = RunningBridge()

    page.seed(seed)
    await _open(user, page)

    assert not element(user, "restore-revision").enabled


async def test_redock_after_load_of_never_run_restore_plans_full_rerun(user: User, page, output_base):
    # Restored, never redocked, then Loaded again: Load resets `pending`,
    # so redock said "nothing to redock" while Save said "click Run Docking
    # first". Load now seeds `pending` from parameters_ran().
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _open(user, page)
    state_manager = _state_manager(page)
    await _restore(user, page, 1)
    assert not state_manager.parameters_ran()

    await _load(user, page)
    assert page.state.form["param_center_x"] == "1.0"  # the restored revision is what loaded
    click(user, "run-docking")

    assert set(page.state.pending_rerun["steps"]) == set(WORKFLOW_STEPS)


@pytest.mark.parametrize("protein_id, kept", [("6lu7", True), ("1abc", False)])
async def test_restore_drops_box_picker_receptor_only_for_another_protein(
    user: User, page, output_base, protein_id, kept
):
    # Phase 12 R1 cleared a stale picker receptor on New Session / a fresh
    # job only; a Load or restore of another protein kept it, so geometry
    # commands computed from the wrong protein.
    revisions = []

    def seed(state):
        state_manager = active_session_with_two_revisions(state, output_base)
        revisions.append(state_manager.save_parameters_checkpoint(
            validated({**REV2_PARAMS, "protein_id": protein_id, "center": [9.0, 9.0, 9.0]}),
            source="interactive_update",
        ))
        state.box_picker_receptor_pdb = str(FIXTURE_PDB)

    page.seed(seed)
    await _open(user, page)

    await _restore(user, page, revisions[0])

    assert (page.state.box_picker_receptor_pdb is not None) is kept


# ---------------------------------------------------------------------------
# Save Session, then Load (ported)
# ---------------------------------------------------------------------------

async def test_save_session_saves_a_real_change_with_defaults(user: User, page, output_base):
    def seed(state):
        completed_session_with_artifacts(state, output_base)
        _form_at_checkpoint(state)

    page.seed(seed)
    await user.open("/")
    state_manager = _state_manager(page)
    set_value(user, "param_center_x", "8.5")

    click(user, "save-session")

    assert page.chat_texts()[-1] == "Parameters saved. Click **Run Docking** to apply them."
    draft = state_manager.load_draft_parameters()
    assert draft["center"] == [8.5, 6.0, 7.0]
    # Same shape as the checkpoint a redock would write -- defaults kept.
    checkpoint = state_manager.load_latest_parameters()
    assert {k: draft[k] for k in ("energy_range", "num_modes", "population")} == {
        k: checkpoint[k] for k in ("energy_range", "num_modes", "population")
    }
    page.state.form["param_center_x"] = "0.0"
    await _load(user, page)
    assert page.state.form["param_center_x"] == "8.5"


async def test_save_session_with_form_back_at_checkpoint_clears_stale_draft(user: User, page, output_base):
    # An edit was saved, then the form was set back to the checkpoint: the
    # old draft stayed on disk, and Load (which prefers a draft) brought the
    # undone edit back.
    def seed(state):
        state_manager = completed_session_with_artifacts(state, output_base)
        state_manager.save_draft_parameters(validated({**REV2_PARAMS, "center": [8.5, 6.0, 7.0]}))
        _form_at_checkpoint(state)

    page.seed(seed)
    await user.open("/")
    state_manager = _state_manager(page)

    click(user, "save-session")

    assert page.chat_texts()[-1] == "No parameter changes to save."
    assert state_manager.load_draft_parameters() is None
    await _load(user, page)
    assert page.state.form["param_center_x"] == "5.0"


async def test_loaded_pre_phase15_session_reads_as_moldina_with_nothing_to_redock(user: User, page, output_base):
    # Its checkpoint has no docking_algorithm key while the form sends
    # "moldina": a key on one side only counted as changed, so every old
    # session planned a bogus dock/visualize redock (and Save Session always
    # found a change).
    def seed(state):
        state_manager = completed_session_with_artifacts(state, output_base)
        old_params = validated(REV2_PARAMS)
        old_params.pop("docking_algorithm")
        state_manager.save_parameters_checkpoint(old_params, source="interactive_update")
        state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="visualize")
        state.form["param_docking_algorithm"] = "vina"

    page.seed(seed)
    await user.open("/")

    await _load(user, page)
    assert element(user, "param_docking_algorithm").value == "moldina"

    click(user, "save-session")
    assert page.chat_texts()[-1] == "No parameter changes to save."
    click(user, "run-docking")
    assert page.chat_texts()[-1] == "No parameter changes detected — nothing to redock."


# ---------------------------------------------------------------------------
# Load, Rename, Delete
# ---------------------------------------------------------------------------

async def test_load_shows_the_session_on_results_and_fills_the_form(user: User, page, output_base):
    job_dir = completed_job_on_disk(output_base, JOB_A)
    WorkflowStateManager(job_dir).save_parameters_checkpoint(validated(REV1_PARAMS), source="initial_parse")

    await _open(user, page)
    click(user, f"load-{JOB_A}")

    state = page.state
    assert state.job_id == JOB_A and state.viewed_job_id == JOB_A and state.job_name == "trial_a"
    assert state.interactive_session.has_active_job()
    assert element(user, "param_center_x").value == "1.0"
    assert page.tab == "Results"
    assert page.results.inner_tabs.value == "Session Viewer"
    assert JOB_A in element(user, "chat-session").content


async def test_load_after_a_run_shows_the_loaded_session_not_the_run(user: User, page, output_base):
    # Stage 16.7's real-data check: the previous run's finished bridge
    # stayed after a Load, so Results drew its result under the loaded
    # session's name.
    completed_job_on_disk(output_base, JOB_A)
    previous = {"status": "completed", "job_name": "previous", "results": {
        "dock": {"results": [{"ligand_id": "GLO", "best_affinity": -5.0, "modes": [{"affinity": -5.0}]}]},
    }}

    def seed(state):
        completed_session_with_artifacts(state, output_base)
        state.bridge = FakeBridge(result=previous)

    page.seed(seed)
    await _open(user, page)
    click(user, f"load-{JOB_A}")
    await results_loaded(page)

    assert "Viewing session" in element(user, "results-status").content
    assert {row["ligand"] for row in page.results._ligand_rows()} == {"ATP", "HEM"}
    assert page.state.bridge is None


async def test_load_is_off_while_a_run_goes(user: User, page, output_base):
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))

    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.bridge = RunningBridge()

    page.seed(seed)
    await _open(user, page)

    assert not element(user, f"load-{JOB_A}").enabled


async def test_rename_saves_the_new_name_and_cancel_keeps_the_old(user: User, page, output_base):
    job_dir = _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    page.seed(lambda state: setattr(state, "viewed_job_id", JOB_A))
    await _open(user, page)
    assert not element(user, f"rename-editor-{JOB_A}").visible

    click(user, f"rename-{JOB_A}")
    assert element(user, f"rename-input-{JOB_A}").value == "trial_a"
    set_value(user, f"rename-input-{JOB_A}", "ignored")
    click(user, f"rename-cancel-{JOB_A}")
    assert not element(user, f"rename-editor-{JOB_A}").visible
    assert json.loads((job_dir / "workflow_results.json").read_text())["job_name"] == "trial_a"

    click(user, f"rename-{JOB_A}")
    set_value(user, f"rename-input-{JOB_A}", "  renamed run  ")
    click(user, f"rename-save-{JOB_A}")
    await eventually(lambda: "renamed run" in _row_text(user, JOB_A))

    assert json.loads((job_dir / "workflow_results.json").read_text())["job_name"] == "renamed run"
    assert page.state.job_name == "renamed run"  # the viewed session's label follows
    assert not element(user, f"rename-editor-{JOB_A}").visible


async def test_a_load_before_the_relist_after_a_rename_uses_the_new_name(
    user: User, page, output_base, monkeypatch
):
    # Found in Review Stage R3's real-data check: the re-list after a rename
    # takes seconds with many sessions, and a Load meanwhile resumed the
    # session under its old name (the row's listing entry).
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    await _open(user, page)
    gate = threading.Event()
    real = sessions_tab._read_listing

    def slow(*args):
        gate.wait(5)
        return real(*args)

    monkeypatch.setattr(sessions_tab, "_read_listing", slow)
    click(user, f"rename-{JOB_A}")
    set_value(user, f"rename-input-{JOB_A}", "renamed run")
    click(user, f"rename-save-{JOB_A}")
    try:
        await eventually(lambda: "renamed run" in _row_text(user, JOB_A))
        assert page.sessions.refreshing  # the re-list is still under way
        click(user, f"load-{JOB_A}")
    finally:
        gate.set()
    await results_loaded(page)

    assert page.state.job_name == "renamed run"


async def test_delete_asks_first_and_removes_the_session_off_the_event_loop(user: User, page, output_base, monkeypatch):
    where = []
    real = core_sessions.remove_session_dir

    def remove(job):
        where.append(on_event_loop())
        return real(job)

    monkeypatch.setattr(core_sessions, "remove_session_dir", remove)
    job_dir = _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    _write_job(output_base, JOB_B, completed_job_data(FIXTURE_PDB))
    await _open(user, page)
    dialog = element(user, "delete-dialog")

    click(user, f"delete-{JOB_A}")
    assert dialog.value is True
    assert JOB_A in element(user, "delete-message").content
    click(user, "delete-cancel")
    assert dialog.value is False and job_dir.exists()

    click(user, f"delete-{JOB_A}")
    click(user, "delete-confirm")
    await eventually(lambda: _rows(page) == [JOB_B])

    assert not job_dir.exists()
    assert where == [False]
    assert page.parts[-1].controller.busy is None


async def test_deleting_the_active_session_starts_a_new_one(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _open(user, page)

    click(user, f"delete-{HISTORY_JOB_ID}")
    click(user, "delete-confirm")
    await eventually(lambda: _rows(page) == [])

    state = page.state
    assert state.job_id is None and not state.interactive_session.has_active_job()
    assert state.messages == [GREETING_MESSAGE]
    assert not element(user, "active-session").visible


async def test_delete_is_off_for_the_running_session_only(user: User, page, output_base):
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))

    def seed(state):
        completed_session_with_artifacts(state, output_base)
        state.bridge = RunningBridge()

    page.seed(seed)
    await _open(user, page)

    assert not element(user, f"delete-{HISTORY_JOB_ID}").enabled
    assert element(user, f"running-{HISTORY_JOB_ID}").visible
    assert element(user, f"delete-{JOB_A}").enabled
    assert not element(user, f"running-{JOB_A}").visible


async def test_load_delete_and_restore_wait_for_a_save(user: User, page, output_base, monkeypatch):
    # Review Stage R2, Deferred #3: a Delete during a save removed the job
    # dir under the copy, and a Load swapped the session it reports to.
    gate, started = threading.Event(), threading.Event()
    real = save_results.write_bundle

    def write_bundle(job):
        started.set()
        gate.wait(5)
        return real(job)

    monkeypatch.setattr(save_results, "write_bundle", write_bundle)
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _open(user, page)
    controller = page.parts[-1].controller

    set_value(user, "chat-input", "save results as trial_a")
    click(user, "chat-send")
    await eventually(started.is_set)
    try:
        assert controller.busy == SAVING
        # Rename too (Review Stage R3): during a delete it wrote into the
        # directory the rmtree was removing.
        for marker in (
            f"load-{JOB_A}", f"load-{HISTORY_JOB_ID}", f"delete-{JOB_A}", f"rename-{JOB_A}",
            f"rename-save-{JOB_A}", "restore-revision",
        ):
            assert not element(user, marker).enabled, marker
    finally:
        gate.set()
    await eventually(lambda: controller.busy is None)

    for marker in (f"load-{JOB_A}", f"delete-{JOB_A}", f"rename-{JOB_A}", "restore-revision"):
        assert element(user, marker).enabled, marker


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

async def test_compare_two_sessions_opens_compare_sessions(user: User, page, output_base):
    completed_job_on_disk(output_base, JOB_A)
    completed_job_on_disk(output_base, JOB_B)
    await _open(user, page)
    compare = element(user, "compare-sessions")

    element(user, f"select-{JOB_A}").value = True
    assert not compare.visible
    element(user, f"select-{JOB_B}").value = True
    assert compare.visible

    click(user, "compare-sessions")
    await eventually(lambda: page.state.comparison is not None)

    assert page.tab == "Results"
    assert page.results.inner_tabs.value == "Compare Sessions"


async def test_a_failed_comparison_is_shown(user: User, page, output_base, monkeypatch):
    def fail(job_dirs):
        raise RuntimeError("unreadable session")

    monkeypatch.setattr(sessions_tab, "compare_jobs", fail)
    completed_job_on_disk(output_base, JOB_A)
    completed_job_on_disk(output_base, JOB_B)
    await _open(user, page)
    element(user, f"select-{JOB_A}").value = True
    element(user, f"select-{JOB_B}").value = True

    click(user, "compare-sessions")
    await eventually(lambda: element(user, "sessions-error").visible)

    assert element(user, "sessions-error").text == "Comparison failed: unreadable session"
    assert page.state.comparison is None and page.tab == "Sessions"


# ---------------------------------------------------------------------------
# The listing's reads
# ---------------------------------------------------------------------------

def _count_listings(monkeypatch) -> list:
    calls = []
    real = core_sessions.list_sessions

    def list_sessions(output_base):
        calls.append(1)
        return real(output_base)

    monkeypatch.setattr(core_sessions, "list_sessions", list_sessions)
    return calls


def _finish_a_run(user, page) -> None:
    """What a run finishing changes for this tab: ``job_done``."""
    with user.client:
        page.state.job_done = not page.state.job_done
        page.parts[-1].controller.sync()


async def test_a_finished_run_relists_but_a_chat_reply_rereads_only_the_revisions(
    user: User, page, output_base, monkeypatch
):
    # Listing parses every session's results file (4-6 s for ~170 sessions
    # on a bind mount); it ran on every chat reply (Review Stage R3).
    calls = _count_listings(monkeypatch)
    page.seed(lambda state: active_session_with_two_revisions(state, output_base))
    await _open(user, page)
    assert calls == [1]
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))

    table = element(user, "revision-table").content
    await send_chat(user, page, "undo 1")
    # The reply re-reads the revisions (the restore added one), not the sessions.
    await eventually(lambda: element(user, "revision-table").content != table)
    await sessions_listed(page)
    assert calls == [1]
    assert JOB_A not in _rows(page)

    _finish_a_run(user, page)
    await eventually(lambda: JOB_A in _rows(page))
    assert calls == [1, 1]


async def test_a_name_being_typed_survives_a_relist(user: User, page, output_base):
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    await _open(user, page)
    click(user, f"rename-{JOB_A}")
    set_value(user, f"rename-input-{JOB_A}", "half-typ")

    _write_job(output_base, JOB_B, completed_job_data(FIXTURE_PDB))
    _finish_a_run(user, page)  # re-lists, with a new row
    await eventually(lambda: JOB_B in _rows(page))

    assert element(user, f"rename-editor-{JOB_A}").visible
    assert element(user, f"rename-input-{JOB_A}").value == "half-typ"


async def test_a_failed_listing_is_shown_and_the_next_read_clears_it(user: User, page, output_base, monkeypatch):
    # Only OSError/ValueError were caught: another failure left the spinner
    # spinning, and a caught one stayed above a later good listing.
    real = sessions_tab._read_listing
    failures = [TypeError("bad results file")]

    def read(*args):
        if failures:
            raise failures.pop()
        return real(*args)

    monkeypatch.setattr(sessions_tab, "_read_listing", read)
    _write_job(output_base, JOB_A, completed_job_data(FIXTURE_PDB))
    await user.open("/")
    open_sessions(user, page)
    await eventually(lambda: not page.sessions.refreshing and page.sessions.read_error)
    assert element(user, "sessions-error").visible
    assert "bad results file" in element(user, "sessions-error").text

    click(user, "sessions-refresh")
    await eventually(lambda: _rows(page) == [JOB_A])
    assert not element(user, "sessions-error").visible


async def test_listing_is_read_off_the_event_loop_and_a_stale_read_is_dropped(user: User, page, monkeypatch):
    where, returned, gate = [], [], threading.Event()
    real = sessions_tab._read_listing
    stale = sessions_tab._Listing(completed=[{"job_id": "stale"}])

    def read(output_base, state_manager, previous=None):
        where.append(on_event_loop())
        if len(where) == 1:
            gate.wait(5)  # opening the tab: still reading when Refresh starts another
            returned.append(stale)
            return stale
        return real(output_base, state_manager, previous)

    monkeypatch.setattr(sessions_tab, "_read_listing", read)
    await user.open("/")
    assert where == []  # nothing is read until the tab is opened
    open_sessions(user, page)
    await eventually(lambda: len(where) == 1)
    click(user, "sessions-refresh")
    await eventually(lambda: page.sessions._listing is not None)
    gate.set()
    await eventually(lambda: returned)
    await asyncio.sleep(0.1)

    assert where == [False, False]
    assert page.sessions._listing is not stale
    assert not page.sessions.refreshing
