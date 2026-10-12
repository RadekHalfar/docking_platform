"""Isolation for the NiceGUI User-fixture tests (Phase 16).

Loaded as a pytest plugin by ui_nice/pytest.ini, so it applies to every test
of the ui-nice tier. It mirrors the AppTest ``app`` fixture in
tests/test_ui_apptest.py: no network, no MCP services, no real orchestrator
threads, nothing written outside tmp_path.

- ``OUTPUT_BASE`` points to a fresh dir in tmp_path. NiceGUI's user_plugin
  runs ui_nice/app.py anew for every test, so the env var is picked up.
- The LLM endpoint is a dead local port, and Ollama's model list is stubbed.
- ``ui.settings.SETTINGS_FILE`` is patched directly: it's frozen at import.
- Any run gets a finished :class:`~tests.ui_fakes.FakeBridge`.

The 3Dmol viewer recorder joins in Stage 16.5, with the viewer itself.
"""
import pytest

from tests.ui_fakes import FakeBridge


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
    monkeypatch.setattr("ui.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("ui.ollama_models.list_ollama_models", lambda *a, **k: [])
    monkeypatch.setattr("ui.orchestrator_bridge.OrchestratorBridge", FakeBridge)
    return output_base
