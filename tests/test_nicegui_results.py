"""The NiceGUI Results tab (Phase 16, Stage 16.6): the Session Viewer,
Save results as…, the Session Log, the downloads and their route, Compare
Sessions, and the switch to Results when a run finishes.

Ported from the former Streamlit tier (mapping: docs/reviews/stage_16_8_parity.md);
each ported test keeps its old name.
Runs in the GUI test tier only (see tests/test_nicegui_setup.py).
"""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("nicegui")

import httpx  # noqa: E402
from nicegui import app as nicegui_app  # noqa: E402
from nicegui.testing import User  # noqa: E402

from ui.core import save_results  # noqa: E402
from ui_nice import results_tab  # noqa: E402
from tests.nicegui_harness import (  # noqa: E402
    HISTORY_JOB_ID,
    active_session_with_two_revisions,
    click,
    completed_job_data,
    completed_job_on_disk,
    completed_session_with_artifacts,
    element,
    eventually,
    on_event_loop,
    open_results,
    results_loaded,
    send_chat,
    set_value,
)
from tests.ui_fakes import FIXTURE_PDB, FakeBridge, RunningBridge  # noqa: E402

JOB_ID = "job_20260930_000001_abcdef"


def _view(page, job_id=JOB_ID):
    page.seed(lambda state: setattr(state, "viewed_job_id", job_id))


def _bundle_meta(job_dir, name):
    return json.loads((job_dir / "saved_results" / name / "metadata.json").read_text())


def _text(user, marker):
    return element(user, marker).content


async def _save_panel(user, page):
    """Open Results and its Save results as… panel, once its block reason is known."""
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)
    element(user, "save-results").value = True
    view = page.results
    await eventually(lambda: view._reason_key == view._save_key())


async def _save_via_panel(user, page, name, scope="all"):
    set_value(user, "bundle-name", name)
    set_value(user, "save-scope", scope)
    await eventually(lambda: element(user, "save-bundle").enabled)
    click(user, "save-bundle")
    await eventually(lambda: page.results.controller.busy is None and page.state.messages[-1]["role"] == "assistant")


def _links(user):
    return [link.props["href"] for link in _all(user, "download-link")]


def _all(user, marker):
    from nicegui import ElementFilter

    with user.client:
        return list(ElementFilter(marker=marker, local_scope=False))


# ---------------------------------------------------------------------------
# Session Viewer (ported)
# ---------------------------------------------------------------------------

async def test_completed_session_from_disk_renders_results_and_viewer(user: User, page, output_base):
    job_dir = output_base / JOB_ID
    job_dir.mkdir()
    receptor = job_dir / "receptor.pdb"
    receptor.write_text(FIXTURE_PDB.read_text())
    (job_dir / "workflow_results.json").write_text(json.dumps(completed_job_data(receptor)))
    _view(page)

    await user.open("/")
    open_results(user, page)
    await results_loaded(page)

    await user.should_see("Viewing session")
    assert "trial" in _text(user, "results-status")
    viewer = element(user, "results-viewer")
    assert viewer.mode == "view"
    assert "ATOM" in viewer.receptor_pdb


@pytest.mark.parametrize("algorithm, label", [("vina", "Vina (Monte Carlo)"), (None, "Moldina (PSO)")])
async def test_results_name_the_algorithm_that_docked(user: User, page, output_base, algorithm, label):
    data = completed_job_data(Path("/nonexistent.pdb"))
    if algorithm:  # None: a result written before Phase 15
        data["results"]["dock"]["docking_algorithm"] = algorithm
    (output_base / JOB_ID).mkdir()
    (output_base / JOB_ID / "workflow_results.json").write_text(json.dumps(data))
    _view(page)

    await user.open("/")
    await results_loaded(page)

    await user.should_see(f"Docked with {label}")


async def test_save_results_sits_between_viewer_and_session_log(user: User, page, output_base):
    # Re-specified for the tab: the viewer (with the pose grids) comes
    # first, then Save results as…, then the Session Log, then downloads.
    def seed(state):
        completed_session_with_artifacts(state, output_base)
        completed_job_on_disk(output_base, HISTORY_JOB_ID)

    page.seed(seed)
    await user.open("/")
    await results_loaded(page)

    view = page.results
    assert view.viewer is not None  # the viewer path, not an early return
    children = list(view.viewer_column)
    assert children.index(view.body) + 1 == children.index(view.save_panel)
    assert children.index(view.save_panel) + 1 == children.index(view.log_panel)
    assert children.index(view.log_panel) + 1 == children.index(view.downloads)
    assert view.save_panel.visible and view.log_panel.visible
    assert view.viewer in list(view.body.descendants())


async def test_no_session_and_no_results_say_so(user: User, page, output_base):
    await user.open("/")
    await results_loaded(page)
    assert element(user, "results-empty").visible

    page.state.viewed_job_id = JOB_ID  # a session with nothing on disk
    page.results.sync()
    await results_loaded(page)
    await user.should_see(results_tab.NO_RESULTS)


async def test_failed_session_shows_its_errors_and_log(user: User, page, output_base):
    job_dir = output_base / JOB_ID
    job_dir.mkdir()
    (job_dir / "workflow_results.json").write_text(
        json.dumps({"status": "failed", "errors": ["no ligands"], "results": {}})
    )
    (job_dir / "job.log").write_text("STEP 1\nERROR: no ligands\n")
    _view(page)

    await user.open("/")
    await results_loaded(page)

    await user.should_see(f"Session `{JOB_ID}` failed.")
    await user.should_see("no ligands")
    assert "ERROR: no ligands" in _text(user, "session-log-text")


async def test_a_result_that_cannot_be_read_is_said_so(user: User, page, output_base, monkeypatch):
    # Only OSError was caught: anything else (a malformed result) left the
    # viewer on what it showed before, and the load was never retried
    # (Review Stage R3).
    completed_job_on_disk(output_base, JOB_ID)

    def malformed(*args):
        raise AttributeError("'NoneType' object has no attribute 'get'")

    monkeypatch.setattr(results_tab.results_view, "read_results_data", malformed)
    _view(page)
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)

    assert "Could not read the results" in _text(user, "results-status")
    assert page.results._loading is None


async def test_a_rename_of_the_viewed_session_follows_into_its_status(user: User, page, output_base):
    # The status line kept the name the session was read with (Review
    # Stage R3).
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)
    assert "renamed run" not in _text(user, "results-status")

    with user.client:
        page.state.job_name = "renamed run"  # what apply_session_rename does
        page.results.controller.sync()

    assert "renamed run" in _text(user, "results-status")


async def test_missing_receptor_is_named(user: User, page, output_base):
    job_dir = completed_job_on_disk(output_base, JOB_ID)
    (job_dir / "visualization" / "receptor.pdb").unlink()
    _view(page)

    await user.open("/")
    await results_loaded(page)

    await user.should_see(results_tab.RECEPTOR_MISSING)
    assert page.results.viewer is None


# ---------------------------------------------------------------------------
# Poses: the ligand and pose grids drive the viewer
# ---------------------------------------------------------------------------

async def test_pose_row_click_swaps_the_viewer_pose_and_keeps_the_viewer(user: User, page, output_base):
    completed_job_on_disk(output_base, JOB_ID)
    _view(page)
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)
    viewer = element(user, "results-viewer")
    assert set(viewer.ligands) == {"ATP", "HEM"}
    assert viewer.ligands["ATP"]["sdf"].startswith("atp_pose_1")
    rows = element(user, "ligand-grid").options["rowData"]
    assert [(r["ligand"], r["best"], r["poses"]) for r in rows] == [("ATP", -8.0, 2), ("HEM", -6.0, 1)]
    assert _text_of(user, "pose-caption") == "Poses of ATP"

    user.find(marker="pose-grid").trigger("cellClicked", {"data": {"mode": 2, "affinity": -7.5}})

    assert element(user, "results-viewer") is viewer  # same viewer: the camera is kept
    assert viewer.ligands["ATP"]["affinity"] == -7.5
    assert viewer.ligands["ATP"]["sdf"].startswith("atp_pose_2")
    assert viewer.ligands["HEM"]["sdf"].startswith("hem_pose_1")
    shown = [r["shown"] for r in element(user, "pose-grid").options["rowData"]]
    assert shown == ["", results_tab.SHOWN]
    assert element(user, "ligand-grid").options["rowData"][0]["shown"] == "Mode 2 (-7.50 kcal/mol)"

    user.find(marker="ligand-grid").trigger("cellClicked", {"data": {"ligand": "HEM"}})
    assert _text_of(user, "pose-caption") == "Poses of HEM"
    assert [r["mode"] for r in element(user, "pose-grid").options["rowData"]] == [1]


def _text_of(user, marker):
    return element(user, marker).text


async def test_affinity_chart_is_drawn(user: User, page, output_base):
    completed_job_on_disk(output_base, JOB_ID)
    _view(page)
    await user.open("/")
    await results_loaded(page)

    chart = element(user, "affinity-chart")
    figures = [e for e in chart.descendants() if type(e).__name__ == "Plotly"]
    assert figures
    assert list(figures[0].figure.data[0].y) == ["ATP", "HEM"]


# ---------------------------------------------------------------------------
# Save results as… (ported)
# ---------------------------------------------------------------------------

async def test_save_results_panel_writes_bundle(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    job_dir = output_base / HISTORY_JOB_ID
    await _save_panel(user, page)

    await _save_via_panel(user, page, "trial_a")

    bundle = job_dir / "saved_results" / "trial_a"
    assert (bundle / "docking_results" / "ATP_out.pdbqt").exists()
    assert (bundle / "visualization" / "session.pml").exists()
    meta = _bundle_meta(job_dir, "trial_a")
    assert meta["scope"] == "all"
    assert meta["parameters"]["center"] == [5.0, 6.0, 7.0]
    assert page.chat_texts()[-1].startswith("Saved bundle `trial_a` (scope: all)")


async def test_save_results_blocked_while_parameters_pending(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    job_dir = output_base / HISTORY_JOB_ID
    await _save_panel(user, page)

    await send_chat(user, page, "undo 1")  # pending = CORE_PARAMS, results predate it
    view = page.results
    await eventually(lambda: view._reason_key == view._save_key())

    assert not element(user, "save-bundle").enabled
    assert "Parameters changed since the last completed run" in _text(user, "save-reason")
    await send_chat(user, page, "save results as trial_a")
    assert page.chat_texts()[-1].startswith("Cannot save: Parameters changed")
    assert not (job_dir / "saved_results").exists()


async def test_save_results_blocked_when_parameters_never_ran_after_load(user: User, page, output_base):
    # Real case found during Stage 13.3: a revision restored but never
    # redocked, then the session Loaded again -- `pending` is reset by the
    # Load, so only the on-disk manifest still shows the results predate it.
    def seed(state):
        completed_session_with_artifacts(state, output_base).restore_revision(1)

    page.seed(seed)
    job_dir = output_base / HISTORY_JOB_ID
    await _save_panel(user, page)

    assert not page.state.interactive_session.pending_changed_params
    assert not element(user, "save-bundle").enabled
    assert "have not been run yet" in _text(user, "save-reason")
    await send_chat(user, page, "save results as trial_a")
    assert "have not been run yet" in page.chat_texts()[-1]
    assert not (job_dir / "saved_results").exists()


async def test_a_failed_save_check_is_shown_not_left_pending(user: User, page, output_base, monkeypatch):
    # An uncaught failure of the background check left the button off with
    # no reason, never checked again (Review Stage R3).
    def unreadable(state, output_base):
        raise PermissionError("[Errno 13] Permission denied: 'workflow_manifest.json'")

    monkeypatch.setattr(save_results, "save_block_reason", unreadable)
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _save_panel(user, page)

    assert not element(user, "save-bundle").enabled
    assert "Could not check whether the results can be saved" in _text(user, "save-reason")
    assert page.results._reason_loading is None


async def test_save_results_conflict_overwrite_and_new_name(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    job_dir = output_base / HISTORY_JOB_ID
    await _save_panel(user, page)
    await _save_via_panel(user, page, "trial_a", scope="all")

    # Same name again, narrower scope -> conflict buttons instead of a save.
    await _save_via_panel(user, page, "trial_a", scope="docking")
    assert "already exists" in page.chat_texts()[-1]
    suggested = page.state.save_conflict["suggested"]
    assert suggested.startswith("trial_a_")
    assert element(user, "save-conflict").visible
    assert element(user, "save-new-name").text == f"Save as {suggested}"

    click(user, "save-new-name")
    await eventually(lambda: page.state.save_conflict is None and page.results.controller.busy is None)
    assert _bundle_meta(job_dir, suggested)["scope"] == "docking"
    assert page.chat_texts()[-1].startswith(f"Saved bundle `{suggested}`")
    assert not element(user, "save-conflict").visible

    await _save_via_panel(user, page, "trial_a", scope="docking")
    click(user, "save-overwrite")
    await eventually(lambda: page.state.save_conflict is None and page.results.controller.busy is None)
    assert _bundle_meta(job_dir, "trial_a")["scope"] == "docking"
    # Overwrite replaces the bundle -- the old "all" save's visualization is gone.
    assert not (job_dir / "saved_results" / "trial_a" / "visualization").exists()


async def test_conflict_cancel_hides_the_buttons(user: User, page, output_base):
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _save_panel(user, page)
    await _save_via_panel(user, page, "trial_a")
    await _save_via_panel(user, page, "trial_a")

    click(user, "save-cancel")

    assert page.state.save_conflict is None
    assert not element(user, "save-conflict").visible


async def test_panel_and_conflict_saves_copy_off_the_event_loop(user: User, page, output_base, monkeypatch):
    # Review Stage R2, Deferred #2: a real copy took 34-37 s with every
    # client frozen.
    copied_on_loop = []
    real_write_bundle = save_results.write_bundle

    def write_bundle(job):
        copied_on_loop.append(on_event_loop())
        return real_write_bundle(job)

    monkeypatch.setattr(save_results, "write_bundle", write_bundle)
    page.seed(lambda state: completed_session_with_artifacts(state, output_base))
    await _save_panel(user, page)
    await _save_via_panel(user, page, "trial_a")
    await _save_via_panel(user, page, "trial_a")
    click(user, "save-overwrite")
    await eventually(lambda: page.state.save_conflict is None and page.results.controller.busy is None)

    assert copied_on_loop == [False, False, False]


async def test_save_panel_only_for_the_active_session(user: User, page, output_base):
    completed_job_on_disk(output_base, JOB_ID)
    _view(page)
    await user.open("/")
    await results_loaded(page)

    assert not element(user, "save-results").visible


# ---------------------------------------------------------------------------
# Downloads (ported) and the route (finding 3)
# ---------------------------------------------------------------------------

async def test_file_downloads_exclude_saved_bundles(user: User, page, output_base):
    def seed(state):
        completed_session_with_artifacts(state, output_base)
        bundle = output_base / HISTORY_JOB_ID / "saved_results" / "trial_a"
        (bundle / "visualization").mkdir(parents=True)
        (bundle / "visualization" / "session.pml").write_text("load x\n")
        (bundle / "metadata.json").write_text("{}")

    page.seed(seed)
    await user.open("/")
    await results_loaded(page)

    links = _links(user)
    assert f"/files/{HISTORY_JOB_ID}/visualization/session.pml" in links  # the session's own files
    assert not [link for link in links if "saved_results" in link]


async def test_downloads_link_each_pose_format_and_the_log(user: User, page, output_base):
    completed_job_on_disk(output_base, JOB_ID)
    _view(page)
    await user.open("/")
    await results_loaded(page)

    links = _links(user)
    assert f"/files/{JOB_ID}/docking_results/ATP_out.pdbqt" in links
    assert f"/files/{JOB_ID}/docking_results/HEM_out.pdbqt" in links
    assert f"/files/{JOB_ID}/job.log" in links
    pdbqt = next(link for link in _all(user, "download-link") if link.props["href"].endswith("ATP_out.pdbqt"))
    assert pdbqt.props["download"] == "ATP_out.pdbqt"

    set_value(user, "pose-format", "sdf")  # no converted .sdf path in this result
    assert not [link for link in _links(user) if link.endswith("_out.pdbqt")]


@pytest.fixture
async def http(user: User):
    """An HTTP client on the NiceGUI app (ui_nice/app.py, loaded by *user*)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=nicegui_app), base_url="http://test") as client:
        yield client


async def test_download_route_serves_a_session_file(http, output_base):
    completed_job_on_disk(output_base, JOB_ID)

    response = await http.get(f"/files/{JOB_ID}/docking_results/ATP_out.pdbqt")

    assert response.status_code == 200
    assert response.content == b"MODEL 1\n"
    assert 'filename="ATP_out.pdbqt"' in response.headers["content-disposition"]


@pytest.mark.parametrize("path", [
    f"/files/{JOB_ID}/..%2Fsecret.txt",
    f"/files/{JOB_ID}/%2e%2e%2Fsecret.txt",
    f"/files/{JOB_ID}/visualization%2F..%2F..%2Fsecret.txt",
    f"/files/{JOB_ID}/saved_results/trial_a/metadata.json",
    f"/files/{JOB_ID}/visualization",
    f"/files/{JOB_ID}/missing.json",
    "/files/uploads/x.sdf",
    "/files/..%2Fsecret.txt/x",
])
async def test_download_route_refuses_anything_outside_the_session(http, output_base, path):
    completed_job_on_disk(output_base, JOB_ID)
    bundle = output_base / JOB_ID / "saved_results" / "trial_a"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text("{}")
    (output_base / "secret.txt").write_text("secret")
    (output_base / "uploads").mkdir()
    (output_base / "uploads" / "x.sdf").write_text("x")

    response = await http.get(path)

    assert response.status_code == 404
    assert b"secret" not in response.content


async def test_download_route_streams_a_large_file(user: User, output_base):
    # Called through ASGI directly: httpx's ASGITransport buffers the body
    # (Stage 16.0 spike, S4).
    big = output_base / JOB_ID / "docking_results" / "big.pdbqt"
    big.parent.mkdir(parents=True)
    big.write_bytes(b"x" * (3 * 1024 * 1024))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": f"/files/{JOB_ID}/docking_results/big.pdbqt", "raw_path": b"", "root_path": "",
        "query_string": b"", "headers": [(b"host", b"test")], "server": ("test", 80), "client": ("test", 1),
    }
    messages = []
    requested = []

    async def receive():
        # As a server does: the request once, then nothing until the client
        # disconnects (Starlette 1.8's FileResponse listens for that, and
        # failed on a second http.request -- found in Review Stage R3).
        if not requested:
            requested.append(1)
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await nicegui_app(scope, receive, send)

    assert messages[0]["status"] == 200
    bodies = [m for m in messages if m["type"] == "http.response.body" and m.get("body")]
    assert len(bodies) > 1
    assert sum(len(m["body"]) for m in bodies) == 3 * 1024 * 1024


# ---------------------------------------------------------------------------
# A running session, and the switch to Results when it ends
# ---------------------------------------------------------------------------

class _FinishesOnRequest(FakeBridge):
    """Running until the test calls :meth:`finish`."""

    def __init__(self, result=None, error=None):
        super().__init__(result=result, error=error)
        self.running = True

    def is_running(self):
        return self.running

    def finish(self):
        self.running = False


@pytest.mark.parametrize("result, error, switches", [
    ({"status": "completed", "results": {}}, None, True),
    ({"status": "failed", "results": {}, "errors": ["boom"]}, None, True),
    (None, "crashed", True),
    ({"status": "cancelled", "results": {}}, None, False),
])
async def test_a_finished_run_opens_the_results_tab(user: User, page, output_base, result, error, switches):
    bridge = _FinishesOnRequest(result=result, error=error)

    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.job_done = False
        state.bridge = bridge

    page.seed(seed)
    await user.open("/")
    assert page.tab == "Setup"

    bridge.finish()
    await eventually(lambda: page.state.job_done)
    await eventually(lambda: page.tab == ("Results" if switches else "Setup"))
    assert page.tab == ("Results" if switches else "Setup")


async def test_running_session_shows_its_log_as_it_grows(user: User, page, output_base):
    log = output_base / HISTORY_JOB_ID / "job.log"

    def seed(state):
        active_session_with_two_revisions(state, output_base)
        log.write_text("STEP 1: Getting input files...\n")
        state.bridge = RunningBridge()

    page.seed(seed)
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)

    await user.should_see("is running...")
    await user.should_see(results_tab.RUNNING_NOTE)
    assert "STEP 1" in _text(user, "session-log-text")
    assert not element(user, "save-results").visible

    with log.open("a") as f:
        f.write("STEP 2: Preparing ligands...\n")
    await eventually(lambda: "STEP 2" in _text(user, "session-log-text"))


async def test_an_unreadable_log_keeps_the_last_one_and_the_tick_going(
    user: User, page, output_base, monkeypatch
):
    # poll() raised, which stopped tick() before its redraw, every second
    # of the run (Review Stage R3).
    log = output_base / HISTORY_JOB_ID / "job.log"

    def seed(state):
        active_session_with_two_revisions(state, output_base)
        log.write_text("STEP 1: Getting input files...\n")
        state.bridge = RunningBridge()

    page.seed(seed)
    await user.open("/")
    open_results(user, page)
    await results_loaded(page)

    def unreadable(*args):
        raise PermissionError("[Errno 13] Permission denied: 'job.log'")

    monkeypatch.setattr(results_tab.results_view, "job_log_if_changed", unreadable)
    synced = []
    controller = page.results.controller
    monkeypatch.setattr(controller, "sync", lambda: synced.append(1))
    with user.client:
        await controller.tick()

    assert synced == [1]
    assert "STEP 1" in _text(user, "session-log-text")


async def test_new_session_empties_the_results_tab(user: User, page, output_base):
    def seed(state):
        completed_session_with_artifacts(state, output_base)
        completed_job_on_disk(output_base, HISTORY_JOB_ID)

    page.seed(seed)
    await user.open("/")
    await results_loaded(page)
    assert page.results.viewer is not None

    click(user, "new-session")
    await results_loaded(page)

    assert element(user, "results-empty").visible
    assert page.results.viewer is None
    assert not element(user, "session-log").visible


# ---------------------------------------------------------------------------
# Compare Sessions
# ---------------------------------------------------------------------------

async def test_comparison_table_is_shown(user: User, page):
    comparison = {
        "jobs": ["job_a", "job_b"],
        "ligands": ["ATP"],
        "affinities": [[-7.1, -8.2]],
        "best_per_ligand": {"ATP": ("job_b", -8.2)},
        "algorithms": ["moldina", "vina"],
    }
    await user.open("/")
    assert element(user, "compare-hint").visible

    page.state.comparison = comparison
    page.results.sync()

    table = element(user, "compare-table")
    assert "-8.20" in table.content and "Vina (Monte Carlo)" in table.content
    assert "#90ee90" in table.content
