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


class RunningBridge(FakeBridge):
    """A docking run that stays running; records Stop and streams *lines*
    once."""

    def __init__(self, lines=()):
        super().__init__()
        self._lines = list(lines)
        self.stop_calls = 0

    def is_running(self):
        return True

    def request_stop(self):
        self.stop_calls += 1

    def stop_requested(self):
        return self.stop_calls > 0

    def get_log_lines(self):
        lines, self._lines = self._lines, []
        return lines


class FinishesWhileStreaming(FakeBridge):
    """Running when first looked at, finished by the time its log is read --
    i.e. the job ends in the middle of a poll."""

    def __init__(self, result):
        super().__init__(result=result)
        self._finished = False

    def is_running(self):
        return not self._finished

    def get_log_lines(self):
        self._finished = True
        return []
