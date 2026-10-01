"""Render-level tests for ui/app.py via Streamlit's AppTest harness (Phase 13).

Complements tests/test_ui_app_helpers.py (non-widget helpers only): these
run the *whole* page script the way `streamlit run` does -- sidebar, chat,
parameters and results panels -- and assert on what actually got rendered.

Same invocation as the rest of the GUI tier: streamlit is only installed in
the `ui` image, so this file skips cleanly under the default suite/CI and
really runs via

    docker compose run --rm ui python -m pytest tests/test_ui_*.py -v

Hermeticity (see the `app` fixture): no network, no MCP services, no real
orchestrator threads, nothing written outside tmp_path. In particular:

- Ollama's model list is stubbed -- the settings form otherwise makes a real
  HTTP request on first render.
- `ui.settings.SETTINGS_FILE` is patched directly: it's frozen at import
  time, and other GUI test files import ui.settings first.
- The 3Dmol.js custom component can't render under AppTest (no browser), so
  `render_mol_viewer` is replaced by a recorder and tests assert on the
  arguments the page passed it.
- Every fake bridge is already *finished*: a running one would make the
  page's bottom-of-script poll (`time.sleep(2); st.rerun()`) loop until the
  AppTest timeout.
"""
import json
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

from common.pdb_geometry import compute_protein_center_and_box

APP_PATH = Path(__file__).resolve().parents[1] / "ui" / "app.py"
FIXTURE_PDB = Path(__file__).parent / "fixtures" / "mini_protein.pdb"


class FakeBridge:
    """Stand-in for ui.orchestrator_bridge.OrchestratorBridge -- always
    finished, never starts a thread."""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.started_with = None

    def start_workflow(self, *args, **kwargs):
        self.started_with = (args, kwargs)

    def is_running(self):
        return False

    def request_stop(self):
        pass

    def stop_requested(self):
        return False

    def get_log_lines(self):
        return []

    def get_result(self):
        return self._result

    def get_error(self):
        return self._error


class ViewerRecorder:
    """Replaces ui.mol_viewer.render_mol_viewer; records each call."""

    def __init__(self):
        self.calls = []
        # What the component "returns" -- e.g. {"center": [...]} to simulate
        # the user clicking a receptor atom in box-picker mode.
        self.box_picker_return = None

    def __call__(self, receptor_pdb, ligands=None, mode="view", center=None,
                 box_size=None, height=500, key=None):
        self.calls.append(
            {
                "receptor_pdb": receptor_pdb,
                "ligands": ligands,
                "mode": mode,
                "center": center,
                "box_size": box_size,
                "key": key,
            }
        )
        return self.box_picker_return if mode == "box_picker" else None

    def last(self, mode):
        matching = [c for c in self.calls if c["mode"] == mode]
        return matching[-1] if matching else None


@pytest.fixture
def viewer():
    return ViewerRecorder()


@pytest.fixture
def output_base(tmp_path):
    base = tmp_path / "orchestrator_output"
    base.mkdir()
    return base


@pytest.fixture
def app(tmp_path, output_base, viewer, monkeypatch):
    """A fresh AppTest for ui/app.py, isolated from network/disk/services."""
    monkeypatch.setenv("OUTPUT_BASE", str(output_base))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr("ui.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: [])
    monkeypatch.setattr("ui.mol_viewer.render_mol_viewer", viewer)
    # Safety net: any button that would start a real orchestrator run gets
    # a finished fake instead.
    monkeypatch.setattr("ui.orchestrator_bridge.OrchestratorBridge", FakeBridge)
    # st.cache_data is process-global (keyed by function source), and a bare
    # `import ui.app` elsewhere in this test session may already have filled
    # it -- e.g. with a real, possibly-failed Ollama model lookup.
    st.cache_data.clear()
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _chat_texts(at) -> list[str]:
    return [
        md.value
        for msg in at.chat_message
        for md in msg.markdown
    ]


def _write_job(output_base: Path, job_id: str, data: dict) -> Path:
    job_dir = output_base / job_id
    job_dir.mkdir()
    (job_dir / "workflow_results.json").write_text(json.dumps(data))
    return job_dir


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_fresh_page_renders_without_exception(app):
    at = app.run()

    assert not at.exception
    subheaders = [s.value for s in at.subheader]
    assert "Chat" in subheaders
    assert "Input & Parameters" in subheaders
    assert "Results" in subheaders
    assert any("No completed sessions yet" in c.value for c in at.sidebar.caption)


# ---------------------------------------------------------------------------
# Box picker
# ---------------------------------------------------------------------------

def test_box_picker_passes_center_and_size_to_viewer(app, viewer):
    app.session_state["_box_picker_receptor_pdb"] = str(FIXTURE_PDB)
    app.session_state["picked_center"] = [1.0, 2.0, 3.0]
    app.session_state["picker_box_x"] = 22.0
    app.session_state["picker_box_y"] = 24.0
    app.session_state["picker_box_z"] = 26.0
    app.session_state["_box_picker_last_size"] = [22.0, 24.0, 26.0]

    at = app.run()

    assert not at.exception
    call = viewer.last("box_picker")
    assert call is not None
    assert call["center"] == [1.0, 2.0, 3.0]
    assert call["box_size"] == [22.0, 24.0, 26.0]
    assert "ATOM" in call["receptor_pdb"]
    assert any("Picked center: (1.00, 2.00, 3.00)" in c.value for c in at.caption)


def test_box_picker_without_receptor_offers_load_button(app, viewer):
    at = app.run()

    assert not at.exception
    assert any(b.label == "Load receptor for box picking" for b in at.button)
    assert viewer.last("box_picker") is None


def test_deferred_geometry_command_applies_and_shows_reply(app, viewer):
    # A chat geometry command started a fetch/prepare job (no receptor was
    # loaded yet); that job has now finished. The page must apply the
    # pending target, reset the bridge, and -- the 2026-09-30 fix -- rerun
    # so the chat reply actually appears (the chat column renders before
    # the box picker does).
    expected_center, _ = compute_protein_center_and_box(FIXTURE_PDB.read_text())
    app.session_state["box_picker_bridge"] = FakeBridge(
        result={
            "status": "completed",
            "results": {
                "prepare_protein": {"prepared_proteins": [{"pdb_path": str(FIXTURE_PDB)}]}
            },
        }
    )
    app.session_state["_pending_geometry_target"] = "center"

    at = app.run()

    assert not at.exception
    assert at.session_state["box_picker_bridge"] is None
    assert "_pending_geometry_target" not in at.session_state
    assert any(t.startswith("Protein center set to") for t in _chat_texts(at))
    assert float(at.session_state["param_center_x"]) == expected_center[0]
    assert viewer.last("box_picker")["center"] == expected_center


def test_deferred_geometry_command_reports_failed_fetch(app):
    app.session_state["box_picker_bridge"] = FakeBridge(
        result={"status": "failed", "errors": ["RCSB download failed"]}
    )
    app.session_state["_pending_geometry_target"] = "box_size"

    at = app.run()

    assert not at.exception
    assert any(
        "Could not fetch the protein: RCSB download failed" in t for t in _chat_texts(at)
    )


# ---------------------------------------------------------------------------
# "Current parameters" summary tracks the Docking Box fields (2026-10-01 fix)
# ---------------------------------------------------------------------------

def _summary_text(at) -> str:
    expanders = [e for e in at.expander if e.label == "Current parameters"]
    assert expanders, "Current parameters summary not rendered"
    return expanders[0].markdown[0].value


def _load_picker_receptor(app, center, size):
    app.session_state["_box_picker_receptor_pdb"] = str(FIXTURE_PDB)
    app.session_state["picked_center"] = center
    app.session_state["picker_box_x"] = size[0]
    app.session_state["picker_box_y"] = size[1]
    app.session_state["picker_box_z"] = size[2]
    app.session_state["_box_picker_last_size"] = list(size)


def test_summary_shows_docking_box_fields_not_stale_parsed_params(app):
    # params["center"]/["box_size"] were set earlier by chat parsing; the
    # Docking Box fields have since changed. The fields are what gets docked.
    app.session_state["params"] = {
        "protein_id": "6lu7",
        "center": [9.0, 9.0, 9.0],
        "box_size": [20.0, 20.0, 20.0],
    }
    for key, value in zip(
        ("param_center_x", "param_center_y", "param_center_z", "param_box_x", "param_box_y", "param_box_z"),
        ("1.000", "2.000", "3.000", "30.000", "31.000", "32.000"),
    ):
        app.session_state[key] = value

    at = app.run()

    assert not at.exception
    summary = _summary_text(at)
    assert "**Center:** 1.000 / 2.000 / 3.000" in summary
    assert "**Box:** 30.000 / 31.000 / 32.000" in summary
    assert "9.0" not in summary


def test_summary_updates_after_box_picker_resize_in_same_run(app):
    # A resize is mirrored into the Docking Box fields by the box picker,
    # which renders *below* the summary -- the summary must still show it.
    _load_picker_receptor(app, [1.0, 2.0, 3.0], (20.0, 20.0, 20.0))
    app.session_state["picker_box_x"] = 30.0

    at = app.run()

    assert not at.exception
    assert "**Box:** 30.000 / 20.000 / 20.000" in _summary_text(at)


def test_summary_updates_after_visual_center_pick(app, viewer):
    _load_picker_receptor(app, None, (20.0, 20.0, 20.0))
    viewer.box_picker_return = {"center": [4.0, 5.0, 6.0]}

    at = app.run()

    assert not at.exception
    assert "**Center:** 4.000 / 5.000 / 6.000" in _summary_text(at)


# ---------------------------------------------------------------------------
# Typing in the Docking Box fields moves the box picker too (2026-10-01 fix)
# ---------------------------------------------------------------------------

def _picker_with_matching_fields(app):
    _load_picker_receptor(app, [1.0, 2.0, 3.0], (20.0, 20.0, 20.0))
    for key, value in zip(_BOX_KEYS, ("1.0", "2.0", "3.0", "20.0", "20.0", "20.0")):
        app.session_state[key] = value
    at = app.run()
    assert not at.exception
    return at


_BOX_KEYS = ("param_center_x", "param_center_y", "param_center_z", "param_box_x", "param_box_y", "param_box_z")


def test_typing_center_in_docking_box_moves_picker_box(app, viewer):
    at = _picker_with_matching_fields(app)

    at.text_input(key="param_center_x").set_value("7.5").run()

    assert not at.exception
    assert at.session_state["picked_center"] == [7.5, 2.0, 3.0]
    assert viewer.last("box_picker")["center"] == [7.5, 2.0, 3.0]
    # The typed value stays as typed -- not overwritten from the picker.
    assert at.text_input(key="param_center_x").value == "7.5"


def test_typing_box_size_in_docking_box_resizes_picker_box(app, viewer):
    at = _picker_with_matching_fields(app)

    at.text_input(key="param_box_x").set_value("30").run()

    assert not at.exception
    assert at.number_input(key="picker_box_x").value == 30.0
    assert viewer.last("box_picker")["box_size"] == [30.0, 20.0, 20.0]
    assert at.text_input(key="param_box_x").value == "30"


def test_docking_box_size_below_picker_minimum_does_not_crash(app, viewer):
    at = _picker_with_matching_fields(app)

    at.text_input(key="param_box_x").set_value("0.5").run()

    assert not at.exception
    assert at.number_input(key="picker_box_x").value == 20.0


# ---------------------------------------------------------------------------
# Redock of a Local Files session (2026-10-01 fix)
# ---------------------------------------------------------------------------

def test_redock_local_files_session_after_ligand_change(app, output_base):
    # Reported: swapping the ligand file in a Local Files session and
    # clicking Run Docking gave "Cannot redock -- required parameters are
    # missing: ligand_ids ..., protein_id ...", and the plan only covered
    # dock/visualize (form-vs-validated default params diffed as changes).
    from services.llm_orchestrator.interactive_session import InteractiveSession
    from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS
    from services.llm_orchestrator.workflow_parser import WorkflowParser
    from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager

    job_id = "job_20261001_000000_abcdef"
    first_run = {
        "protein_file": "/workspace/orchestrator_output/uploads/receptor.pdb",
        "ligand_files": ["/workspace/orchestrator_output/uploads/crystal_ligand.mol2"],
        "center": [1.0, 2.0, 3.0],
        "box_size": [20.0, 20.0, 20.0],
    }
    # What _handle_run_docking checkpoints: the validator's output, defaults filled in.
    validated = WorkflowParser().validate_workflow(
        {"services": list(WORKFLOW_STEPS), "parameters": dict(first_run)}
    )["parameters"]
    state_manager = WorkflowStateManager(output_base / job_id)
    state_manager.save_parameters_checkpoint(validated, source="initial_parse")
    # The first run's steps ran with those parameters (as a real run records it).
    state_manager.save_execution_state_checkpoint({"prepared_protein": "/x/receptor.pdbqt"}, step="visualize")
    session = InteractiveSession()
    session.activate(
        job_id, state_manager, last_results={"status": "completed", "services_executed": list(WORKFLOW_STEPS)}
    )

    app.session_state["interactive_session"] = session
    app.session_state["job_id"] = job_id
    app.session_state["input_mode"] = "local_files"
    app.session_state["params"] = {
        **first_run,
        "ligand_files": ["/workspace/orchestrator_output/uploads/actives_final.sdf"],
    }
    for key, value in zip(_BOX_KEYS, ("1.0", "2.0", "3.0", "20.0", "20.0", "20.0")):
        app.session_state[key] = value

    at = app.run()
    next(b for b in at.button if b.label == "Run Docking").click().run()

    assert not at.exception
    assert not [e.value for e in at.error], [e.value for e in at.error]
    pending = at.session_state["_pending_rerun"]
    assert pending["steps"] == ["get_input_files", "prepare_ligands", "dock", "visualize"]
    latest = state_manager.load_latest_parameters()
    assert latest["ligand_files"] == ["/workspace/orchestrator_output/uploads/actives_final.sdf"]
    # Defaults the first run had are kept, not dropped to None.
    for key in ("energy_range", "num_modes", "population"):
        assert latest[key] == validated[key]


# ---------------------------------------------------------------------------
# Run completion (chat panel)
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
def test_finished_run_reply_is_rendered_in_chat(app, result, error, expected):
    app.session_state["bridge"] = FakeBridge(result=result, error=error)
    app.session_state["job_id"] = "job_20260930_000000_abcdef"
    app.session_state["messages"] = [{"role": "user", "content": "dock ATP into 6lu7"}]

    at = app.run()

    assert not at.exception
    assert at.session_state["_job_done"] is True
    assert expected in _chat_texts(at)


# ---------------------------------------------------------------------------
# Results panel + sidebar history (read straight from disk)
# ---------------------------------------------------------------------------

def _completed_job_data(receptor_path: Path) -> dict:
    return {
        "status": "completed",
        "job_name": "trial_a",
        "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        "results": {
            "get_input_files": {"protein_path": "/app/orchestrator_output/x/downloads/6LU7.pdb"},
            "dock": {
                "results": [
                    {"ligand_id": "ATP", "best_affinity": -7.1, "modes": [{"affinity": -7.1}]}
                ]
            },
            "visualize": {"receptor_pdb": str(receptor_path), "converted": []},
        },
    }


def test_completed_session_from_disk_renders_results_and_viewer(app, viewer, output_base):
    job_id = "job_20260930_000001_abcdef"
    job_dir = output_base / job_id
    job_dir.mkdir()
    receptor = job_dir / "receptor.pdb"
    receptor.write_text(FIXTURE_PDB.read_text())
    (job_dir / "workflow_results.json").write_text(json.dumps(_completed_job_data(receptor)))
    app.session_state["viewed_job_id"] = job_id

    at = app.run()

    assert not at.exception
    assert any("Viewing session" in i.value and "trial" in i.value for i in at.info)
    view_call = viewer.last("view")
    assert view_call is not None
    assert view_call["key"] == f"mol_viewer_{job_id}"
    assert "ATOM" in view_call["receptor_pdb"]


def test_sidebar_lists_completed_and_failed_sessions(app, output_base):
    _write_job(output_base, "job_20260930_000002_aaaaaa", _completed_job_data(Path("/nonexistent.pdb")))
    _write_job(
        output_base,
        "job_20260930_000003_bbbbbb",
        {"status": "failed", "job_name": "", "errors": ["no ligands"], "results": {}},
    )

    at = app.run()

    assert not at.exception
    sidebar_checkboxes = [c.label for c in at.sidebar.checkbox]
    assert any("trial" in label and "job_20260930_000002_aaaaaa" in label for label in sidebar_checkboxes)
    assert any("job_20260930_000003_bbbbbb" in m.value for m in at.sidebar.markdown)
    load_keys = {b.key for b in at.sidebar.button if b.label == "Load"}
    assert "job_load_job_20260930_000002_aaaaaa" in load_keys
    assert "job_load_job_20260930_000003_bbbbbb" in load_keys


# ---------------------------------------------------------------------------
# Revision history + undo (Stage 13.2)
# ---------------------------------------------------------------------------

_HISTORY_JOB_ID = "job_20261001_000002_cafe01"
_REV1_PARAMS = {
    "protein_id": "6lu7",
    "ligand_ids": ["ATP"],
    "center": [1.0, 2.0, 3.0],
    "box_size": [20.0, 20.0, 20.0],
}
_REV2_PARAMS = {**_REV1_PARAMS, "center": [5.0, 6.0, 7.0]}


def _validated(params: dict) -> dict:
    """What the GUI checkpoints: the validator's output, defaults filled in."""
    from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS
    from services.llm_orchestrator.workflow_parser import WorkflowParser

    return WorkflowParser().validate_workflow(
        {"services": list(WORKFLOW_STEPS), "parameters": dict(params)}
    )["parameters"]


def _active_session_with_two_revisions(app, output_base):
    """An active, completed RCSB session: rev 1 = initial params, rev 2 =
    center changed, rev 3 = its redock's state. The form shows rev 2."""
    from services.llm_orchestrator.interactive_session import InteractiveSession
    from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS
    from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager

    state_manager = WorkflowStateManager(output_base / _HISTORY_JOB_ID)
    state_manager.save_parameters_checkpoint(_validated(_REV1_PARAMS), source="initial_parse")
    state_manager.save_parameters_checkpoint(_validated(_REV2_PARAMS), source="interactive_update")
    # ...and redocked: rev 3 records a step running with rev 2's parameters.
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="visualize")
    session = InteractiveSession()
    session.activate(
        _HISTORY_JOB_ID,
        state_manager,
        last_results={"status": "completed", "services_executed": list(WORKFLOW_STEPS)},
    )

    app.session_state["interactive_session"] = session
    app.session_state["job_id"] = _HISTORY_JOB_ID
    app.session_state["_job_done"] = True
    app.session_state["input_mode"] = "rcsb"
    app.session_state["params"] = dict(_REV2_PARAMS)
    app.session_state["param_protein_id"] = "6lu7"
    app.session_state["param_ligand_ids_text"] = "ATP"
    for key, value in zip(_BOX_KEYS, ("5.0", "6.0", "7.0", "20.0", "20.0", "20.0")):
        app.session_state[key] = value
    return session, state_manager


def _click(at, label):
    return next(b for b in at.button if b.label == label).click().run()


def _restore_rev1(at):
    at.selectbox(key="revision_to_restore").set_value(1).run()
    return _click(at, "Restore revision")


def test_revision_history_expander_lists_revisions(app, output_base):
    _active_session_with_two_revisions(app, output_base)

    at = app.run()

    assert not at.exception
    history = next(e for e in at.expander if e.label == "Revision history")
    table = "\n".join(m.value for m in history.markdown)
    assert "| 1 |" in table and "| 2 |" in table
    assert "initial\\_parse" in table
    assert any("currently at revision 2" in c.value for c in history.caption)
    assert at.selectbox(key="revision_to_restore").options == ["2", "1"]


def test_revision_history_hidden_without_active_session(app):
    at = app.run()

    assert not at.exception
    assert not [e for e in at.expander if e.label == "Revision history"]


def test_restore_revision_loads_its_params_and_marks_all_pending(app, output_base):
    from services.llm_orchestrator.workflow_definition import CORE_PARAMS

    session, state_manager = _active_session_with_two_revisions(app, output_base)
    state_manager.save_draft_parameters({**_REV2_PARAMS, "center": [9.0, 9.0, 9.0]})

    at = _restore_rev1(app.run())

    assert not at.exception
    assert at.session_state["param_center_x"] == "1.0"
    assert at.session_state["params"]["center"] == [1.0, 2.0, 3.0]
    # The picker follows the restored box too.
    assert at.session_state["picked_center"] == [1.0, 2.0, 3.0]
    # A draft would otherwise win over the restore on the next Load.
    assert state_manager.load_draft_parameters() is None
    assert state_manager.load_latest_parameters()["center"] == [1.0, 2.0, 3.0]
    assert state_manager.get_parameters_revision() == 4
    assert session.pending_changed_params == set(CORE_PARAMS)
    assert "Restored revision 1" in (output_base / _HISTORY_JOB_ID / "job.log").read_text()
    assert any(t.startswith("Restored revision 1.") for t in _chat_texts(at))


def test_redock_after_restore_plans_full_rerun(app, output_base):
    # Without pending_changed_params the form equals the (restored)
    # checkpoint and redock would be a no-op over the newer results.
    from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS

    _, state_manager = _active_session_with_two_revisions(app, output_base)

    at = _click(_restore_rev1(app.run()), "Run Docking")

    assert not at.exception
    assert not [e.value for e in at.error], [e.value for e in at.error]
    assert set(at.session_state["_pending_rerun"]["steps"]) == set(WORKFLOW_STEPS)
    # Nothing differed from the restored checkpoint -> no extra revision.
    assert state_manager.get_parameters_revision() == 4


def test_cancelled_redock_plan_is_offered_again(app, output_base):
    # The redock checkpoints the new params before "Confirm redock"; after
    # Cancel the form equals that checkpoint, so a plain diff found nothing.
    _active_session_with_two_revisions(app, output_base)
    app.session_state["param_center_x"] = "8.0"

    at = _click(app.run(), "Run Docking")
    first_steps = at.session_state["_pending_rerun"]["steps"]
    at = _click(at, "Cancel")
    assert "_pending_rerun" not in at.session_state
    at = _click(at, "Run Docking")

    assert not at.exception
    assert at.session_state["_pending_rerun"]["steps"] == first_steps
    assert "No parameter changes detected — nothing to redock." not in _chat_texts(at)


@pytest.mark.parametrize("status, cleared", [("completed", True), ("cancelled", False), ("failed", False)])
def test_finished_run_clears_pending_only_when_completed(app, output_base, status, cleared):
    session, _ = _active_session_with_two_revisions(app, output_base)
    session.pending_changed_params |= {"center"}
    app.session_state["_job_done"] = False
    app.session_state["bridge"] = FakeBridge(result={"status": status, "results": {}, "errors": ["x"]})

    at = app.run()

    assert not at.exception
    assert session.pending_changed_params == (set() if cleared else {"center"})


class _RunningBridge(FakeBridge):
    def is_running(self):
        return True


class _StopPolling(Exception):
    pass


@pytest.fixture
def running_bridge(app, monkeypatch):
    """A bridge that stays running. The page's bottom-of-script poll
    (`time.sleep(2); st.rerun()`) would then loop forever, so app.py's own
    sleep raises instead -- everything above it (all three panels) has
    rendered by then, which is what these tests assert on."""
    import sys
    import time

    real_sleep = time.sleep

    def _sleep(seconds):
        if sys._getframe(1).f_code.co_filename.endswith("app.py"):
            raise _StopPolling()
        real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", _sleep)
    app.session_state["bridge"] = _RunningBridge()


def _only_stop_polling_exception(at):
    assert len(at.exception) == 1
    assert "raise _StopPolling()" in at.exception[0].stack_trace[-1]


def test_restore_disabled_while_job_runs(app, output_base, running_bridge):
    _active_session_with_two_revisions(app, output_base)

    at = app.run()

    _only_stop_polling_exception(at)
    restore = next(b for b in at.button if b.label == "Restore revision")
    assert restore.disabled


def test_chat_undo_refused_while_job_runs(app, output_base, running_bridge):
    _, state_manager = _active_session_with_two_revisions(app, output_base)

    at = app.run()
    at.chat_input[0].set_value("undo 1").run()

    _only_stop_polling_exception(at)
    assert "A session is running — Stop it before restoring a revision." in _chat_texts(at)
    assert state_manager.get_parameters_revision() == 2


def test_chat_show_history_and_undo(app, output_base):
    session, state_manager = _active_session_with_two_revisions(app, output_base)

    at = app.run()
    at.chat_input[0].set_value("show history").run()
    assert not at.exception
    reply = _chat_texts(at)[-1]
    assert "| 2 |" in reply and "currently at revision **2**" in reply

    at.chat_input[0].set_value("undo").run()
    assert "Which revision?" in _chat_texts(at)[-1]

    at.chat_input[0].set_value("undo 99").run()
    assert _chat_texts(at)[-1].startswith("Cannot restore revision 99")
    assert state_manager.get_parameters_revision() == 2

    at.chat_input[0].set_value("undo 1").run()
    assert not at.exception
    assert _chat_texts(at)[-1].startswith("Restored revision 1.")
    assert at.session_state["param_center_x"] == "1.0"
    assert state_manager.load_latest_parameters()["center"] == [1.0, 2.0, 3.0]
    assert session.pending_changed_params


@pytest.mark.parametrize("trigger", ["run_completed", "chat_message"])
def test_param_widgets_survive_early_rerun_in_chat_column(app, output_base, trigger):
    # Reported: after a successful docking the Docking Box fields were empty.
    # Both triggers call st.rerun() from the chat column, i.e. before the
    # parameters panel renders; Streamlit's stale-widget cleanup runs on a
    # rerun-interrupted run too, dropping every keyed widget not yet
    # rendered, and init() then re-seeded the empty defaults.
    _active_session_with_two_revisions(app, output_base)
    at = app.run()  # widgets render once, as in the real page
    # Typed by the user, so the values live in widget state -- the state
    # the cleanup drops.
    typed = {"param_center_x": "8.0", "param_box_z": "30.0", "param_ph": "6.5"}
    for key, value in typed.items():
        at.text_input(key=key).set_value(value)
    at.run()

    if trigger == "run_completed":
        at.session_state["_job_done"] = False
        at.session_state["bridge"] = FakeBridge(result={"status": "completed", "results": {}})
        at.run()
    else:
        at.chat_input[0].set_value("show history").run()

    assert not at.exception
    assert [at.session_state[k] for k in _BOX_KEYS] == ["8.0", "6.0", "7.0", "20.0", "20.0", "30.0"]
    assert at.text_input(key="param_center_x").value == "8.0"
    assert at.session_state["input_mode"] == "rcsb"
    assert at.session_state["param_protein_id"] == "6lu7"
    assert at.session_state["param_ph"] == "6.5"
    # Re-assigning widget keys every run must not trip Streamlit's
    # "default value but also set via the Session State API" warning.
    assert not [w.value for w in at.warning if "Session State" in w.value]


def test_chat_history_without_active_session(app):
    at = app.run()
    at.chat_input[0].set_value("show history").run()

    assert not at.exception
    assert _chat_texts(at)[-1] == "No active session — no history yet."


# ---------------------------------------------------------------------------
# Save results as (Stage 13.3)
# ---------------------------------------------------------------------------

def _completed_session_with_artifacts(app, output_base):
    """_active_session_with_two_revisions plus a completed run on disk with
    docking and visualization outputs to bundle."""
    session, state_manager = _active_session_with_two_revisions(app, output_base)
    # Revision 2's parameters actually ran (as a real run records it).
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="dock")
    job_dir = output_base / _HISTORY_JOB_ID
    (job_dir / "workflow_results.json").write_text(
        json.dumps(_completed_job_data(Path("/nonexistent.pdb")))
    )
    (job_dir / "docking_results").mkdir()
    (job_dir / "docking_results" / "ATP_out.pdbqt").write_text("MODEL 1\n")
    (job_dir / "visualization").mkdir()
    (job_dir / "visualization" / "session.pml").write_text("load x\n")
    return session, state_manager, job_dir


def _save_via_panel(at, name, scope="all"):
    at.text_input(key="save_bundle_name").set_value(name)
    at.selectbox(key="save_scope").set_value(scope)
    return _click(at.run(), "Save results bundle")


def _bundle_meta(job_dir, name):
    return json.loads((job_dir / "saved_results" / name / "metadata.json").read_text())


def test_save_results_panel_writes_bundle(app, output_base):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)

    at = _save_via_panel(app.run(), "trial_a")

    assert not at.exception
    bundle = job_dir / "saved_results" / "trial_a"
    assert (bundle / "docking_results" / "ATP_out.pdbqt").exists()
    assert (bundle / "visualization" / "session.pml").exists()
    meta = _bundle_meta(job_dir, "trial_a")
    assert meta["scope"] == "all"
    assert meta["parameters"]["center"] == [5.0, 6.0, 7.0]
    assert _chat_texts(at)[-1].startswith("Saved bundle `trial_a` (scope: all)")


def test_save_results_blocked_while_parameters_pending(app, output_base):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)

    at = _restore_rev1(app.run())  # pending = CORE_PARAMS, results predate it

    assert not at.exception
    save = next(b for b in at.button if b.label == "Save results bundle")
    assert save.disabled
    assert any("Parameters changed since the last completed run" in c.value for c in at.caption)
    at.chat_input[0].set_value("save results as trial_a").run()
    assert _chat_texts(at)[-1].startswith("Cannot save: Parameters changed")
    assert not (job_dir / "saved_results").exists()


def test_save_results_blocked_when_parameters_never_ran_after_load(app, output_base):
    # Real case found during Stage 13.3: a revision restored but never
    # redocked, then the session Loaded again -- `pending` is reset by the
    # Load, so only the on-disk manifest still shows the results predate it.
    session, state_manager, job_dir = _completed_session_with_artifacts(app, output_base)
    state_manager.restore_revision(1)
    assert not session.pending_changed_params

    at = app.run()

    assert not at.exception
    assert next(b for b in at.button if b.label == "Save results bundle").disabled
    assert any("have not been run yet" in c.value for c in at.caption)
    at.chat_input[0].set_value("save results as trial_a").run()
    assert "have not been run yet" in _chat_texts(at)[-1]
    assert not (job_dir / "saved_results").exists()


def test_chat_save_refused_while_job_runs(app, output_base, running_bridge):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)

    at = app.run()
    at.chat_input[0].set_value("save results as trial_a").run()

    _only_stop_polling_exception(at)
    assert _chat_texts(at)[-1].startswith("Cannot save: A session is running")
    assert not (job_dir / "saved_results").exists()


def test_save_results_conflict_overwrite_and_new_name(app, output_base):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)
    at = _save_via_panel(app.run(), "trial_a", scope="all")

    # Same name again, narrower scope -> conflict buttons instead of a save.
    at = _save_via_panel(at, "trial_a", scope="docking")
    assert "already exists" in _chat_texts(at)[-1]
    suggested = at.session_state["_save_conflict"]["suggested"]
    assert suggested.startswith("trial_a_")

    at = _click(at, f"Save as {suggested}")
    assert not at.exception
    assert _bundle_meta(job_dir, suggested)["scope"] == "docking"
    assert _chat_texts(at)[-1].startswith(f"Saved bundle `{suggested}`")
    assert "_save_conflict" not in at.session_state

    at = _save_via_panel(at, "trial_a", scope="docking")
    at = _click(at, "Overwrite")
    assert not at.exception
    assert _bundle_meta(job_dir, "trial_a")["scope"] == "docking"
    # Overwrite replaces the bundle -- the old "all" save's visualization is gone.
    assert not (job_dir / "saved_results" / "trial_a" / "visualization").exists()


def test_chat_save_docking_as_name(app, output_base):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)

    at = app.run()
    at.chat_input[0].set_value("save docking as dock_only").run()

    assert not at.exception
    assert _bundle_meta(job_dir, "dock_only")["scope"] == "docking"
    assert not (job_dir / "saved_results" / "dock_only" / "visualization").exists()

    at.chat_input[0].set_value("save results").run()
    assert _chat_texts(at)[-1] == "Give the bundle a name, e.g. `save results as run1`."


def test_file_downloads_exclude_saved_bundles(app, output_base):
    _completed_session_with_artifacts(app, output_base)
    at = _save_via_panel(app.run(), "trial_a")

    download_keys = [b.proto.id for b in at.get("download_button")]
    assert download_keys  # the session's own files are still listed
    assert not [k for k in download_keys if "saved_results" in k]


def test_save_bundle_name_survives_early_rerun_in_chat_column(app, output_base):
    _completed_session_with_artifacts(app, output_base)
    at = app.run()
    at.text_input(key="save_bundle_name").set_value("typed_name")
    at.selectbox(key="save_scope").set_value("visualization")
    at.run()

    at.chat_input[0].set_value("show history").run()

    assert not at.exception
    assert at.text_input(key="save_bundle_name").value == "typed_name"
    assert at.selectbox(key="save_scope").value == "visualization"


def test_save_results_sits_between_viewer_and_session_log(app, viewer, output_base):
    _, _, job_dir = _completed_session_with_artifacts(app, output_base)
    receptor = job_dir / "receptor.pdb"
    receptor.write_text(FIXTURE_PDB.read_text())
    (job_dir / "workflow_results.json").write_text(json.dumps(_completed_job_data(receptor)))

    at = app.run()

    assert not at.exception
    assert viewer.last("view") is not None  # the viewer path, not an early return
    labels = [e.label for e in at.expander]
    assert labels.index("Save results as…") + 1 == labels.index("Session Log")


# ---------------------------------------------------------------------------
# Review Stage R1 (Phase 13) regressions
# ---------------------------------------------------------------------------

@pytest.fixture
def session_state_warnings(monkeypatch):
    """Streamlit's "default value but also set via the Session State API"
    policy warning goes to its logger, not the page -- record it there."""
    import streamlit.elements.lib.policies as policies

    recorded = []
    monkeypatch.setattr(policies._LOGGER, "warning", lambda msg, *a, **k: recorded.append(msg % a if a else msg))
    return recorded


def test_picker_box_size_survives_early_rerun_in_chat_column(app, output_base, session_state_warnings):
    # A size typed into the picker's own input came back at the old value
    # after any chat-column rerun, and the picker's resize mirror then wrote
    # that value into the Docking Box fields actually docked with.
    _active_session_with_two_revisions(app, output_base)
    app.session_state["_box_picker_receptor_pdb"] = str(FIXTURE_PDB)
    app.session_state["picked_center"] = [5.0, 6.0, 7.0]
    at = app.run()
    at.number_input(key="picker_box_x").set_value(61.0).run()
    assert at.session_state["param_box_x"] == "61.000"

    at.chat_input[0].set_value("show history").run()

    assert not at.exception
    assert [at.session_state[k] for k in _BOX_KEYS[3:]] == ["61.000", "20.000", "20.000"]
    assert at.number_input(key="picker_box_x").value == 61.0
    assert not [w for w in session_state_warnings if "Session State" in w]


def test_receptor_load_with_box_fields_set_logs_no_session_state_warning(app, viewer, session_state_warnings):
    for key, value in zip(_BOX_KEYS, ("1.0", "2.0", "3.0", "30.0", "31.0", "32.0")):
        app.session_state[key] = value
    app.session_state["box_picker_bridge"] = FakeBridge(
        result={
            "status": "completed",
            "results": {"prepare_protein": {"prepared_proteins": [{"pdb_path": str(FIXTURE_PDB)}]}},
        }
    )

    at = app.run()

    assert not at.exception
    assert viewer.last("box_picker")["box_size"] == [30.0, 31.0, 32.0]
    assert not [w for w in session_state_warnings if "Session State" in w]


def test_redock_after_load_of_never_run_restore_plans_full_rerun(app, output_base):
    # Restored, never redocked, then Loaded again: Load resets `pending`,
    # so redock said "nothing to redock" while Save said "click Run Docking
    # first". Load now seeds `pending` from parameters_ran().
    from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS

    session, state_manager, _ = _completed_session_with_artifacts(app, output_base)
    at = _restore_rev1(app.run())
    assert not state_manager.parameters_ran()

    at = next(b for b in at.sidebar.button if b.key == f"job_load_{_HISTORY_JOB_ID}").click().run()
    assert not at.exception
    assert at.session_state["param_center_x"] == "1.0"  # the restored revision is what loaded
    at = _click(at, "Run Docking")

    assert not at.exception
    assert set(at.session_state["_pending_rerun"]["steps"]) == set(WORKFLOW_STEPS)


@pytest.mark.parametrize("change_center", [False, True])
def test_redock_after_stopped_first_run_docks_again(app, output_base, change_center):
    # Stopped during the first run's dock step: dock/visualize never ran,
    # and intersecting the plan with the executed steps dropped them --
    # both "same params" and "changed center" were dead ends.
    session, state_manager = _active_session_with_two_revisions(app, output_base)
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="prepare_protein")
    session.last_results = {
        "status": "cancelled",
        "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein"],
    }
    at = app.run()
    if change_center:
        at.text_input(key="param_center_x").set_value("9.0").run()

    at = _click(at, "Run Docking")

    assert not at.exception
    assert at.session_state["_pending_rerun"]["steps"] == ["dock", "visualize"]


def test_chat_undo_of_state_only_revision_is_refused(app, output_base):
    session, state_manager = _active_session_with_two_revisions(app, output_base)
    state_rev = state_manager.save_execution_state_checkpoint({"protein_file": "/x/a.pdbqt"}, step="dock")
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/b.pdbqt"}, step="visualize")
    at = app.run()

    at.chat_input[0].set_value(f"undo {state_rev}").run()

    assert not at.exception
    assert "has no parameter checkpoint" in _chat_texts(at)[-1]
    assert state_manager.load_latest_state()["protein_file"] == "/x/b.pdbqt"
    assert state_manager.get_parameters_revision() == 2


@pytest.mark.parametrize("protein_id, kept", [("6lu7", True), ("1abc", False)])
def test_restore_drops_box_picker_receptor_only_for_another_protein(app, output_base, protein_id, kept):
    # Phase 12 R1 cleared a stale picker receptor on New Session / a fresh
    # job only; a Load or restore of another protein kept it, so geometry
    # commands computed from the wrong protein.
    session, state_manager = _active_session_with_two_revisions(app, output_base)
    other_rev = state_manager.save_parameters_checkpoint(
        _validated({**_REV2_PARAMS, "protein_id": protein_id, "center": [9.0, 9.0, 9.0]}),
        source="interactive_update",
    )
    app.session_state["_box_picker_receptor_pdb"] = str(FIXTURE_PDB)
    at = app.run()
    at.selectbox(key="revision_to_restore").set_value(other_rev).run()

    at = _click(at, "Restore revision")

    assert not at.exception
    assert ("_box_picker_receptor_pdb" in at.session_state) is kept
