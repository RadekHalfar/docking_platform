"""Test doubles for the GUI tests: NiceGUI's User-fixture cases
(tests/test_nicegui_*.py, harness in tests/nicegui_harness.py) and the
hermetic ui/core tests.

No GUI framework import, so the hermetic suite can load it.
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


def finish_inline(state, planned):
    """Run a save ``ui.core.save_results`` planned -- ``(message, job)`` --
    all inline, as the GUI does with the copy off its event loop: the reply,
    or the plan's message (None with nothing to do)."""
    from ui.core import save_results

    message, job = planned
    if job is None:
        return message
    return save_results.finish_save(state, job, save_results.write_bundle(job))


def inline_run_save(state, output_base):
    """A ``chat.submit`` *run_save* that saves inline and posts the reply,
    like ``PageController.run_save`` without the event loop."""
    from ui.core import save_results
    from ui.core.state import reply

    def run_save(intent):
        message = finish_inline(state, save_results.plan_chat_save(state, output_base, intent))
        if message:
            reply(state, message)

    return run_save
