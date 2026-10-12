"""The NiceGUI Setup tab (Phase 16, Stage 16.4): form, Advanced, actions,
redock dialog and live progress.

Ports the Setup rows of the parity table (tests/test_nicegui_parity.py)
from tests/test_ui_apptest.py; each ported test keeps its AppTest name.
Runs in the ui-nice tier only:

    docker compose run --rm --no-deps ui-nice \
        python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py

``page.seed(...)`` sets up the client's GuiSession before the page is
drawn, as the AppTest cases set ``app.session_state``.
"""
import pytest

pytest.importorskip("nicegui")

from nicegui.elements.upload_files import SmallFileUpload  # noqa: E402
from nicegui.testing import User  # noqa: E402

from services.llm_orchestrator.interactive_session import InteractiveSession  # noqa: E402
from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS  # noqa: E402
from services.llm_orchestrator.workflow_parser import WorkflowParser  # noqa: E402
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager  # noqa: E402
from ui.core.form import BOX_FORM_KEYS  # noqa: E402
from ui.core.state import GREETING_MESSAGE  # noqa: E402
from ui.worker_cap import MEASURED_BEST_WORKERS, WorkerCap  # noqa: E402
from tests.nicegui_harness import (  # noqa: E402
    REV2_PARAMS,
    active_session_with_two_revisions,
    click,
    element,
    eventually,
    open_advanced,
    set_value,
    validated,
    with_worker_cap,
)
from tests.ui_fakes import FakeBridge, FinishesWhileStreaming, RunningBridge  # noqa: E402

CAP10 = WorkerCap(10, "compose")


def _two_revisions(page, output_base, **form):
    """Seed active_session_with_two_revisions, plus these form values."""
    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.form.update(form)

    page.seed(seed)


def _form_at_checkpoint():
    # The fixture's checkpoint holds the validator's max_workers default; a
    # real GUI run checkpoints the form's own value, so match it here.
    return {"param_max_workers": validated(REV2_PARAMS)["max_workers"]}


def _state_manager(page):
    return page.state.interactive_session.state_manager


# ---------------------------------------------------------------------------
# Worker cap (Stage 14.1)
# ---------------------------------------------------------------------------

async def test_worker_cap_from_compose_shows_limit_caption(user: User, page):
    with_worker_cap(page, CAP10)
    await user.open("/")

    caption = element(user, "worker-cap-caption").text
    assert "Docking service is limited to 10 CPUs (docker-compose.yml)" in caption
    assert f"Benchmarked best for Moldina (PSO): {MEASURED_BEST_WORKERS['moldina']} workers" in caption
    assert not element(user, "worker-cap-warning").visible
    # Stage 14.3: a fresh session defaults to the measured best, not the cap.
    assert element(user, "param_max_workers").value == min(MEASURED_BEST_WORKERS["moldina"], 10)


async def test_worker_cap_fallback_shows_warning_with_reason(user: User, page):
    reason = "docker-compose.yml not found at /nowhere/docker-compose.yml"
    with_worker_cap(page, WorkerCap(3, "fallback", reason))
    await user.open("/")

    warning = element(user, "worker-cap-warning")
    assert warning.visible
    assert "Couldn't read the docking service's CPU limit" in warning.text and reason in warning.text
    assert element(user, "param_max_workers").max == 3


# ---------------------------------------------------------------------------
# "Current parameters" summary
# ---------------------------------------------------------------------------

async def test_summary_shows_docking_box_fields_not_stale_parsed_params(user: User, page):
    def seed(state):
        state.params = {"protein_id": "6lu7", "center": [9.0, 9.0, 9.0], "box_size": [20.0, 20.0, 20.0]}
        state.form.update(zip(BOX_FORM_KEYS, ("1.000", "2.000", "3.000", "30.000", "31.000", "32.000")))

    page.seed(seed)
    await user.open("/")

    summary = element(user, "params-summary").content
    assert "**Center:** 1.000 / 2.000 / 3.000" in summary
    assert "**Box:** 30.000 / 31.000 / 32.000" in summary
    assert "9.0" not in summary


async def test_summary_follows_a_typed_box_field(user: User, page):
    page.seed(lambda state: state.params.update(protein_id="6lu7"))
    await user.open("/")

    set_value(user, "param_center_x", "4.5")

    assert "**Center:** 4.5 / — / —" in element(user, "params-summary").content


async def test_summary_shows_the_selected_algorithm(user: User, page):
    page.seed(lambda state: state.params.update(protein_id="6lu7"))
    await user.open("/")
    open_advanced(user)

    set_value(user, "param_docking_algorithm", "vina")

    assert "**Algorithm:** Vina (Monte Carlo)" in element(user, "params-summary").content


# ---------------------------------------------------------------------------
# Run Docking / redock
# ---------------------------------------------------------------------------

async def test_run_docking_with_missing_params_replies_without_rerun(user: User, page):
    await user.open("/")

    click(user, "run-docking")

    assert page.chat_texts()[-1].startswith("Cannot start — required parameters missing")
    await user.should_see("Cannot start")


async def test_run_docking_starts_a_job_and_shows_its_progress(user: User, page, monkeypatch):
    started = []

    def make_bridge():
        bridge = RunningBridge(lines=["STEP 1: get_input_files"])
        started.append(bridge)
        return bridge

    monkeypatch.setattr("ui.orchestrator_bridge.OrchestratorBridge", make_bridge)

    def seed(state):
        state.form.update(input_mode="rcsb", param_protein_id="6lu7", param_ligand_ids_text="ATP")
        state.form.update(zip(BOX_FORM_KEYS, ("1", "2", "3", "20", "20", "20")))

    page.seed(seed)
    await user.open("/")

    click(user, "run-docking")

    state = page.state
    assert started and state.bridge is started[0]
    assert page.chat_texts()[-1] == f"Starting docking session `{state.job_id}`..."
    # The timer streams the log into the Run log, not into the chat.
    await eventually(lambda: state.run_log == ["STEP 1: get_input_files"])
    assert page.chat_texts()[-1] == f"Starting docking session `{state.job_id}`..."
    await eventually(lambda: element(user, "run-status").visible)
    assert element(user, "run-log").visible
    # Run Docking / New Session / Reset are off while the run goes.
    assert not element(user, "run-docking").enabled
    assert not element(user, "reset").enabled


async def test_redock_local_files_session_after_ligand_change(user: User, page, output_base):
    job_id = "job_20261001_000000_abcdef"
    first_run = {
        "protein_file": "/workspace/orchestrator_output/uploads/receptor.pdb",
        "ligand_files": ["/workspace/orchestrator_output/uploads/crystal_ligand.mol2"],
        "center": [1.0, 2.0, 3.0],
        "box_size": [20.0, 20.0, 20.0],
    }
    checkpoint = WorkflowParser().validate_workflow(
        {"services": list(WORKFLOW_STEPS), "parameters": dict(first_run)}
    )["parameters"]

    def seed(state):
        state_manager = WorkflowStateManager(output_base / job_id)
        state_manager.save_parameters_checkpoint(checkpoint, source="initial_parse")
        state_manager.save_execution_state_checkpoint({"prepared_protein": "/x/receptor.pdbqt"}, step="visualize")
        session = InteractiveSession()
        session.activate(
            job_id, state_manager,
            last_results={"status": "completed", "services_executed": list(WORKFLOW_STEPS)},
        )
        state.interactive_session = session
        state.job_id = job_id
        state.form["input_mode"] = "local_files"
        state.params = {**first_run, "ligand_files": ["/workspace/orchestrator_output/uploads/actives_final.sdf"]}
        state.form.update(zip(BOX_FORM_KEYS, ("1.0", "2.0", "3.0", "20.0", "20.0", "20.0")))

    page.seed(seed)
    await user.open("/")

    click(user, "run-docking")

    assert not element(user, "action-error").visible
    pending = page.state.pending_rerun
    assert pending["steps"] == ["get_input_files", "prepare_ligands", "dock", "visualize"]
    latest = _state_manager(page).load_latest_parameters()
    assert latest["ligand_files"] == ["/workspace/orchestrator_output/uploads/actives_final.sdf"]
    for key in ("energy_range", "num_modes", "population"):
        assert latest[key] == checkpoint[key]


async def test_redock_dialog_shows_the_plan_and_confirm_runs_it(user: User, page, output_base):
    _two_revisions(page, output_base, param_center_x="8.0")
    await user.open("/")

    click(user, "run-docking")

    dialog = element(user, "redock-dialog")
    assert dialog.value is True
    assert element(user, "redock-plan").text.startswith("Rerun plan:")
    click(user, "confirm-redock")

    state = page.state
    assert state.pending_rerun is None
    assert dialog.value is False
    args, _ = state.bridge.started_with
    assert args[0]["services"] == ["dock", "visualize"]
    assert page.chat_texts()[-1].startswith("Redocking `job_20261001_000002_cafe01` — steps: dock, visualize")


async def test_cancelled_redock_plan_is_offered_again(user: User, page, output_base):
    _two_revisions(page, output_base, param_center_x="8.0")
    await user.open("/")

    click(user, "run-docking")
    first_steps = page.state.pending_rerun["steps"]
    click(user, "cancel-redock")
    assert page.state.pending_rerun is None
    assert element(user, "redock-dialog").value is False
    click(user, "run-docking")

    assert page.state.pending_rerun["steps"] == first_steps
    assert "No parameter changes detected — nothing to redock." not in page.chat_texts()


@pytest.mark.parametrize("change_center", [False, True])
async def test_redock_after_stopped_first_run_docks_again(user: User, page, output_base, change_center):
    def seed(state):
        state_manager = active_session_with_two_revisions(state, output_base)
        state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="prepare_protein")
        state.interactive_session.last_results = {
            "status": "cancelled",
            "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein"],
        }

    page.seed(seed)
    await user.open("/")
    if change_center:
        set_value(user, "param_center_x", "9.0")

    click(user, "run-docking")

    assert page.state.pending_rerun["steps"] == ["dock", "visualize"]


async def test_redock_with_nothing_changed_replies_without_rerun(user: User, page, output_base):
    _two_revisions(page, output_base, **_form_at_checkpoint())
    await user.open("/")

    click(user, "run-docking")

    assert page.chat_texts()[-1] == "No parameter changes detected — nothing to redock."
    assert page.state.pending_rerun is None


async def test_switching_algorithm_redocks_dock_and_visualize_only(user: User, page, output_base):
    with_worker_cap(page, CAP10)
    _two_revisions(page, output_base)
    await user.open("/")
    open_advanced(user)
    set_value(user, "param_docking_algorithm", "vina")

    click(user, "run-docking")

    assert page.state.pending_rerun["steps"] == ["dock", "visualize"]
    latest = _state_manager(page).load_latest_parameters()
    assert latest["docking_algorithm"] == "vina"
    assert latest["cpu"] == 1


async def test_pso_mode_sends_one_cpu_whatever_the_cpu_field_holds(user: User, page, output_base):
    _two_revisions(page, output_base, param_cpu="4", **_form_at_checkpoint())  # left over from Vina mode
    await user.open("/")

    click(user, "run-docking")

    assert page.chat_texts()[-1] == "No parameter changes detected — nothing to redock."


# ---------------------------------------------------------------------------
# Advanced: algorithm, CPUs per ligand, workers (Stage 15.3)
# ---------------------------------------------------------------------------

async def test_cpu_field_shows_only_for_vina_and_bounds_workers(user: User, page):
    with_worker_cap(page, CAP10)
    await user.open("/")
    open_advanced(user)
    assert element(user, "param_docking_algorithm").value == "moldina"
    assert not element(user, "param_cpu").visible
    await user.should_see("Moldina (PSO) uses 1 CPU per ligand")

    set_value(user, "param_docking_algorithm", "vina")

    assert element(user, "param_cpu").visible
    # Switching resets workers to Vina's benchmarked best.
    workers = element(user, "param_max_workers")
    assert workers.value == min(MEASURED_BEST_WORKERS["vina"], 10)
    assert (f"Benchmarked best for Vina (Monte Carlo): {MEASURED_BEST_WORKERS['vina']} workers"
            in element(user, "worker-cap-caption").text)

    set_value(user, "param_cpu", 4)

    assert workers.max == 10 // 4
    assert workers.value == 10 // 4  # clamped, so workers x cpu stays within the quota
    assert page.state.form["param_max_workers"] == 10 // 4


async def test_cleared_cpu_field_is_not_written(user: User, page):
    # A number input (Phase 15 Future Work): a cleared field means the user
    # is typing a new number, not "1" by fallback.
    page.seed(lambda state: state.form.update(param_docking_algorithm="vina", param_cpu=3))
    await user.open("/")
    open_advanced(user)

    set_value(user, "param_cpu", None)

    assert page.state.form["param_cpu"] == 3


async def test_workers_field_follows_the_parallel_checkbox(user: User, page):
    await user.open("/")
    open_advanced(user)
    workers = element(user, "param_max_workers")
    assert not workers.enabled

    set_value(user, "param_parallel", True)

    assert workers.enabled and page.state.form["param_parallel"] is True


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

async def test_input_mode_shows_its_own_inputs(user: User, page):
    await user.open("/")
    await user.should_see(marker="upload-protein")  # Local Files by default
    await user.should_not_see(marker="param_ligand_ids_text")

    set_value(user, "input_mode", "rcsb")

    assert page.state.form["input_mode"] == "rcsb"
    await user.should_not_see(marker="upload-protein")
    await user.should_see(marker="param_ligand_ids_text")


async def test_protein_id_is_confirmed_once_when_committed(user: User, page):
    page.seed(lambda state: state.form.update(input_mode="rcsb"))
    await user.open("/")

    set_value(user, "param_protein_id", "6lu7")
    assert page.state.params == {}  # not per keystroke

    field = user.find(marker="param_protein_id")
    field.trigger("blur")
    field.trigger("keydown.enter")

    assert page.state.params["protein_id"] == "6lu7"
    assert page.chat_texts().count("Updated protein ID: **6lu7**") == 1


async def test_protein_upload_is_saved_and_docked_against(user: User, page, output_base):
    page.seed(lambda state: state.params.update(protein_id="6lu7"))
    await user.open("/")
    upload = element(user, "upload-protein")

    await upload.handle_uploads([SmallFileUpload("../receptor.pdb", "chemical/x-pdb", b"ATOM\n")])

    saved = output_base / "uploads" / "receptor.pdb"
    await eventually(lambda: page.state.params.get("protein_file") == str(saved))
    assert saved.read_bytes() == b"ATOM\n"
    assert "protein_id" not in page.state.params
    assert page.chat_texts()[-1] == "Local protein file set: **../receptor.pdb**"
    await user.should_see("Protein: receptor.pdb")


async def test_ligand_uploads_replace_the_ligand_list(user: User, page, output_base):
    await user.open("/")
    upload = element(user, "upload-ligands")

    await upload.handle_uploads([
        SmallFileUpload("a.sdf", "chemical/x-mdl-sdfile", b"a"),
        SmallFileUpload("b.mol2", "chemical/x-mol2", b"b"),
    ])

    await eventually(lambda: len(page.state.params.get("ligand_files", [])) == 2)
    assert page.chat_texts()[-1] == "2 local ligand file(s) set: **a.sdf, b.mol2**"
    await user.should_see("2 ligand file(s) ready")


# ---------------------------------------------------------------------------
# Save Session / New Session / Reset
# ---------------------------------------------------------------------------

async def test_save_session_replies_without_rerun(user: User, page, output_base):
    _two_revisions(page, output_base, **_form_at_checkpoint())
    await user.open("/")

    click(user, "save-session")

    assert page.chat_texts()[-1] == "No parameter changes to save."
    assert _state_manager(page).load_draft_parameters() is None


async def test_save_and_new_session_need_an_active_session(user: User, page):
    await user.open("/")
    assert not element(user, "new-session").enabled
    assert not element(user, "save-session").enabled


async def test_new_session_detaches_and_keeps_the_form(user: User, page, output_base):
    _two_revisions(page, output_base)
    await user.open("/")
    assert element(user, "new-session").enabled

    click(user, "new-session")

    state = page.state
    assert state.job_id is None and not state.interactive_session.has_active_job()
    assert state.messages == [GREETING_MESSAGE]
    assert element(user, "param_center_x").value == "5.0"
    assert not element(user, "new-session").enabled


async def test_reset_clears_the_form_but_keeps_llm_settings(user: User, page, output_base):
    _two_revisions(page, output_base)
    await user.open("/")
    settings, client = page.state.settings, page.state.llm_client

    click(user, "reset")

    state = page.state
    assert state.job_id is None and state.params == {}
    assert state.form["input_mode"] == "local_files"
    assert element(user, "param_center_x").value == ""
    assert element(user, "input_mode").value == "local_files"
    assert state.settings == settings and state.llm_client is client
    assert state.messages == [GREETING_MESSAGE]


# ---------------------------------------------------------------------------
# A run finishing; Stop; live progress
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "result, error, expected",
    [
        ({"status": "completed", "results": {}}, None, "Workflow completed successfully!"),
        ({"status": "failed", "errors": ["dock exploded"]}, None, "Workflow failed: dock exploded"),
        ({"status": "cancelled"}, None, "Session stopped."),
        (None, "boom", "Workflow failed: boom"),
    ],
)
async def test_finished_run_reply_is_rendered_in_chat(user: User, page, result, error, expected):
    def seed(state):
        state.bridge = FakeBridge(result=result, error=error)
        state.job_id = "job_20260930_000000_abcdef"
        state.messages = [{"role": "user", "content": "dock ATP into 6lu7"}]

    page.seed(seed)
    await user.open("/")

    assert page.state.job_done is True
    assert expected in page.chat_texts()
    await user.should_see(expected)


@pytest.mark.parametrize("status, cleared", [("completed", True), ("cancelled", False), ("failed", False)])
async def test_finished_run_clears_pending_only_when_completed(user: User, page, output_base, status, cleared):
    def seed(state):
        active_session_with_two_revisions(state, output_base)
        state.interactive_session.pending_changed_params |= {"center"}
        state.job_done = False
        state.bridge = FakeBridge(result={"status": status, "results": {}, "errors": ["x"]})

    page.seed(seed)
    await user.open("/")

    assert page.state.interactive_session.pending_changed_params == (set() if cleared else {"center"})


async def test_job_finishing_mid_run_is_handled_by_one_more_poll(user: User, page):
    # A job that ends while a poll reads it is handled by the next tick.
    def seed(state):
        state.messages = [{"role": "assistant", "content": "Running..."}]
        state.job_id = "job_20260930_000000_abcdef"
        state.job_done = False
        state.bridge = FinishesWhileStreaming({"status": "completed", "results": {}})

    page.seed(seed)
    await user.open("/")

    await eventually(lambda: page.state.job_done)
    assert page.chat_texts()[-1] == "Workflow completed successfully!"
    await user.should_see("Workflow completed successfully!")


async def test_stop_asks_the_running_job_to_stop(user: User, page):
    bridge = RunningBridge()

    def seed(state):
        state.bridge = bridge
        state.job_id = "job_20260930_000000_abcdef"

    page.seed(seed)
    await user.open("/")
    assert element(user, "run-status").visible
    await user.should_see("is running...")

    click(user, "stop")

    assert bridge.stop_calls == 1
    assert not element(user, "stop").visible
    await user.should_see("Stop requested")


async def test_progress_shows_the_ligands_docked(user: User, page, output_base):
    job_id = "job_20260930_000000_abcdef"
    progress_dir = output_base / job_id / "docking_results"
    progress_dir.mkdir(parents=True)
    (progress_dir / "docking_progress.json").write_text(
        '{"status": "running", "total": 4, "completed": 1, "in_progress": [{"ligand": "ATP"}]}'
    )

    def seed(state):
        state.bridge = RunningBridge()
        state.job_id = job_id

    page.seed(seed)
    await user.open("/")

    await user.should_see("1 / 4 ligands docked")
    await user.should_see("Currently docking: ATP")
