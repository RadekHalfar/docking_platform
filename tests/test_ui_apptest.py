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
