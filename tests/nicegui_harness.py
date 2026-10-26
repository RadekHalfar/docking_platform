"""Isolation and helpers for the NiceGUI User-fixture tests (Phase 16).

Loaded as a pytest plugin by ui_nice/pytest.ini, so it applies to every test
of the GUI test tier: no network, no MCP services, no real orchestrator
threads, nothing written outside tmp_path.

- ``OUTPUT_BASE`` points to a fresh dir in tmp_path. NiceGUI's user_plugin
  runs ui_nice/app.py anew for every test, so the env var is picked up.
- The LLM settings come from fixed env vars: Ollama at a dead local port,
  a fixed model, and no API key or base URL (``docker compose run`` passes
  the ``.env`` ones, real key included). Ollama's model list is stubbed.
- ``ui.settings.SETTINGS_FILE`` is patched directly: it's frozen at import.
- Any run gets a finished :class:`~tests.ui_fakes.FakeBridge`.
- The progress timer ticks every 50 ms instead of every second.
- :class:`Page` (the autouse ``page`` fixture) seeds each client's
  ``GuiSession`` before its page is drawn, and keeps every page built.

The 3Dmol viewer needs no recorder (Stage 16.5): a
:class:`~ui_nice.mol_viewer.MolViewer` keeps what it last sent to the
browser (``receptor_pdb``, ``center``, ``box_size``, ...), and tests read
that from the element (:func:`viewer`). :func:`atom_click` fires the event
a click on a receptor atom sends.

The Results tab (Stage 16.6) reads a result off the event loop: tests wait
for :func:`results_loaded`. :func:`completed_job_on_disk` writes a
completed session with a receptor and multi-pose SDFs to view.

The Sessions tab (Stage 16.7) reads its listing off the event loop too:
:func:`open_sessions` opens the tab as a user does, and tests wait for
:func:`sessions_listed`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

import pytest
from nicegui import ElementFilter

from services.llm_orchestrator.interactive_session import InteractiveSession
from services.llm_orchestrator.workflow_definition import WORKFLOW_STEPS
from services.llm_orchestrator.workflow_parser import WorkflowParser
from services.llm_orchestrator.workflow_state_manager import WorkflowStateManager
from ui.core.form import BOX_FORM_KEYS
from ui.worker_cap import WorkerCap, default_worker_count
from tests.ui_fakes import FIXTURE_PDB, FakeBridge


@pytest.fixture
def output_base(tmp_path):
    base = tmp_path / "orchestrator_output"
    base.mkdir()
    return base


@pytest.fixture(autouse=True)
def nice_env(tmp_path, output_base, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(output_base))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setattr("ui.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: [])
    monkeypatch.setattr("ui.orchestrator_bridge.OrchestratorBridge", FakeBridge)
    monkeypatch.setattr("ui_nice.controller.POLL_INTERVAL_S", 0.05)
    return output_base


class Page:
    """Every page built in a test, and the seeds applied before drawing."""

    def __init__(self) -> None:
        self.seeds: list[Callable] = []
        self.states: list = []
        self.parts: list = []

    def seed(self, seed: Callable) -> Callable:
        """Run ``seed(state)`` on each new client's GuiSession before its
        page is drawn."""
        self.seeds.append(seed)
        return seed

    @property
    def state(self):
        """The last page's GuiSession."""
        return self.states[-1]

    @property
    def chat(self):
        return self.parts[-1].chat

    @property
    def results(self):
        """The last page's Results tab view."""
        return self.parts[-1].results

    @property
    def sessions(self):
        """The last page's Sessions tab view."""
        return self.parts[-1].sessions

    @property
    def tab(self) -> str:
        """The last page's open main-area tab."""
        return self.parts[-1].tabs.value

    def chat_texts(self) -> list[str]:
        return [m["content"] for m in self.state.messages]


@pytest.fixture(autouse=True)
def page(monkeypatch) -> Page:
    """Autouse, so the patch is in place before the user fixture runs
    ui_nice/app.py (which imports build_page by name)."""
    import ui_nice.layout as layout

    recorder = Page()
    real_build_page = layout.build_page

    def build_page(state, output_base):
        for seed in recorder.seeds:
            seed(state)
        recorder.states.append(state)
        parts = real_build_page(state, output_base)
        recorder.parts.append(parts)
        return parts

    monkeypatch.setattr(layout, "build_page", build_page)
    return recorder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def eventually(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Wait until *predicate()* holds (an async handler or a timer tick
    has run), or fail after *timeout* seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def on_event_loop() -> bool:
    """Whether the caller runs on the event loop (not in ``run.io_bound``'s
    thread pool): for patched slow functions to record where they ran."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def element(user, marker: str):
    """The (first) element with this marker, hidden or not -- unlike
    ``user.find``, which sees only visible ones (not e.g. the contents of a
    closed expansion), so a test can assert that something is hidden."""
    with user.client:
        elements = list(ElementFilter(marker=marker, local_scope=False))
    assert elements, f"no element marked {marker!r}"
    return elements[0]


def set_value(user, marker: str, value) -> None:
    """Change a visible field as a user would; its change handler runs."""
    elements = user.find(marker=marker).elements
    assert elements, f"no visible element marked {marker!r}"
    next(iter(elements)).value = value


def open_advanced(user) -> None:
    """Expand the Setup tab's Advanced section, as a user must first."""
    element(user, "advanced").value = True


async def send_chat(user, page: Page, text: str) -> None:
    """Send *text* from the chat input and wait for its reply (an LLM
    parse, a geometry read and a save run off the event loop)."""
    set_value(user, "chat-input", text)
    user.find(marker="chat-send").click()
    await eventually(
        lambda: not page.chat.parsing
        and page.chat.controller.busy is None
        and any(m["role"] == "user" and m["content"] == text for m in page.state.messages)
        and page.state.messages[-1]["role"] == "assistant"
    )


def click(user, marker: str) -> None:
    user.find(marker=marker).click()


BOX_PICKER_VIEWER = "box-picker-viewer"


def viewer_shown(user) -> bool:
    with user.client:
        return bool(list(ElementFilter(marker=BOX_PICKER_VIEWER, local_scope=False)))


async def viewer(user):
    """The box picker's viewer, once its receptor has been read (that read
    runs off the event loop)."""
    await eventually(lambda: viewer_shown(user))
    return element(user, BOX_PICKER_VIEWER)


def atom_click(user, center) -> None:
    """A click on a receptor atom at *center*, as the viewer reports it."""
    user.find(marker=BOX_PICKER_VIEWER).trigger("atom_click", {"center": list(center)})


def load_picker_receptor(page: Page, center, size) -> None:
    """A receptor loaded for box picking, with these Docking Box fields
    (strings, as typed)."""
    def seed(state):
        state.box_picker_receptor_pdb = str(FIXTURE_PDB)
        for key, value in zip(BOX_FORM_KEYS, (*center, *size)):
            state.form[key] = value

    page.seed(seed)


def with_worker_cap(page: Page, cap: WorkerCap) -> None:
    """Dock against *cap*, whatever the real compose says."""
    def seed(state):
        state.worker_cap = cap
        state.form["param_max_workers"] = default_worker_count(cap)

    page.seed(seed)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

HISTORY_JOB_ID = "job_20261001_000002_cafe01"
REV1_PARAMS = {
    "protein_id": "6lu7",
    "ligand_ids": ["ATP"],
    "center": [1.0, 2.0, 3.0],
    "box_size": [20.0, 20.0, 20.0],
}
REV2_PARAMS = {**REV1_PARAMS, "center": [5.0, 6.0, 7.0]}


def validated(params: dict) -> dict:
    """What the GUI checkpoints: the validator's output, defaults filled in."""
    return WorkflowParser().validate_workflow(
        {"services": list(WORKFLOW_STEPS), "parameters": dict(params)}
    )["parameters"]


def active_session_with_two_revisions(state, output_base: Path) -> WorkflowStateManager:
    """An active, completed RCSB session: rev 1 = initial params, rev 2 =
    center changed, rev 3 = its redock's state. The form shows rev 2."""
    state_manager = WorkflowStateManager(Path(output_base) / HISTORY_JOB_ID)
    state_manager.save_parameters_checkpoint(validated(REV1_PARAMS), source="initial_parse")
    state_manager.save_parameters_checkpoint(validated(REV2_PARAMS), source="interactive_update")
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="visualize")
    session = InteractiveSession()
    session.activate(
        HISTORY_JOB_ID,
        state_manager,
        last_results={"status": "completed", "services_executed": list(WORKFLOW_STEPS)},
    )
    state.interactive_session = session
    state.job_id = HISTORY_JOB_ID
    state.job_done = True
    state.params = dict(REV2_PARAMS)
    state.form.update(input_mode="rcsb", param_protein_id="6lu7", param_ligand_ids_text="ATP")
    for key, value in zip(BOX_FORM_KEYS, ("5.0", "6.0", "7.0", "20.0", "20.0", "20.0")):
        state.form[key] = value
    return state_manager


def completed_job_data(receptor_path: Path) -> dict:
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


def completed_session_with_artifacts(state, output_base: Path) -> WorkflowStateManager:
    """active_session_with_two_revisions plus a completed run on disk with
    docking and visualization outputs to bundle."""
    state_manager = active_session_with_two_revisions(state, output_base)
    # Revision 2's parameters actually ran (as a real run records it).
    state_manager.save_execution_state_checkpoint({"protein_file": "/x/6lu7.pdbqt"}, step="dock")
    job_dir = Path(output_base) / HISTORY_JOB_ID
    (job_dir / "workflow_results.json").write_text(json.dumps(completed_job_data(Path("/nonexistent.pdb"))))
    (job_dir / "docking_results").mkdir()
    (job_dir / "docking_results" / "ATP_out.pdbqt").write_text("MODEL 1\n")
    (job_dir / "visualization").mkdir()
    (job_dir / "visualization" / "session.pml").write_text("load x\n")
    return state_manager


# ---------------------------------------------------------------------------
# The Results tab (Stage 16.6)
# ---------------------------------------------------------------------------

async def results_loaded(page: Page) -> None:
    """Wait until the Results tab shows what the state asks for (its reads
    run off the event loop)."""
    view = page.results
    await eventually(lambda: view._loading is None and view._loaded == view._load_key())


def open_results(user, page: Page) -> None:
    """Open the Results tab, as a user clicks it."""
    with user.client:
        page.parts[-1].tabs.set_value("Results")


def sdf(*names: str) -> str:
    """A multi-record SDF, one record per name."""
    return "".join(f"{n}\n  mol\n\n  0  0  0  0  0  0            999 V2000\nM  END\n$$$$\n" for n in names)


def completed_job_on_disk(output_base: Path, job_id: str, **extra) -> Path:
    """A completed session that ran visualize: FIXTURE_PDB as the receptor,
    ATP with two poses and HEM with one, their pdbqt files, a .pml and a
    job.log. *extra* goes into workflow_results.json."""
    job_dir = Path(output_base) / job_id
    for sub in ("docking_results", "visualization"):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    receptor = job_dir / "visualization" / "receptor.pdb"
    receptor.write_text(FIXTURE_PDB.read_text())
    (job_dir / "visualization" / "ATP.sdf").write_text(sdf("atp_pose_1", "atp_pose_2"))
    (job_dir / "visualization" / "HEM.sdf").write_text(sdf("hem_pose_1"))
    (job_dir / "visualization" / "session.pml").write_text("load x\n")
    for ligand in ("ATP", "HEM"):
        (job_dir / "docking_results" / f"{ligand}_out.pdbqt").write_text("MODEL 1\n")
    (job_dir / "job.log").write_text("STEP 1: Getting input files...\n")
    mount = f"/app/orchestrator_output/{job_id}"
    data = {
        "status": "completed",
        "job_name": "trial_a",
        "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
        "results": {
            "dock": {
                "docking_algorithm": "vina",
                "results": [
                    {"ligand_id": "ATP", "best_affinity": -8.0, "modes": [{"affinity": -8.0}, {"affinity": -7.5}],
                     "output_path": f"{mount}/docking_results/ATP_out.pdbqt"},
                    {"ligand_id": "HEM", "best_affinity": -6.0, "modes": [{"affinity": -6.0}],
                     "output_path": f"{mount}/docking_results/HEM_out.pdbqt"},
                ],
            },
            "visualize": {
                "receptor_pdb": f"{mount}/visualization/receptor.pdb",
                "converted": [
                    {"ligand_id": "ATP", "sdf_path": f"{mount}/visualization/ATP.sdf"},
                    {"ligand_id": "HEM", "sdf_path": f"{mount}/visualization/HEM.sdf"},
                ],
            },
        },
        **extra,
    }
    (job_dir / "workflow_results.json").write_text(json.dumps(data))
    return job_dir


# ---------------------------------------------------------------------------
# The Sessions tab (Stage 16.7)
# ---------------------------------------------------------------------------

def open_sessions(user, page: Page) -> None:
    """Open the Sessions tab, as a user clicks it (that starts a listing)."""
    with user.client:
        page.parts[-1].tabs.set_value("Sessions")


async def sessions_listed(page: Page) -> None:
    """Wait until the Sessions tab's listing is read for the current state."""
    view = page.sessions
    await eventually(
        lambda: not view.refreshing and view._listing is not None and view._refresh_key == view._change_key()
    )
