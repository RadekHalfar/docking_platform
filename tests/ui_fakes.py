"""Test doubles shared by both GUI test tiers: Streamlit's AppTest cases
(tests/test_ui_apptest.py) and NiceGUI's User-fixture cases
(tests/test_nicegui_*.py, harness in tests/nicegui_harness.py).

No GUI framework import, so either tier's image can load it.
"""
from pathlib import Path

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


def prep_done_bridge():
    """A finished "Load receptor for box picking" job that prepared FIXTURE_PDB."""
    return FakeBridge(
        result={
            "status": "completed",
            "results": {"prepare_protein": {"prepared_proteins": [{"pdb_path": str(FIXTURE_PDB)}]}},
        }
    )
