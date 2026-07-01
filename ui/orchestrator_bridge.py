"""Thread-safe wrapper for running execute_workflow() in the background."""
import contextlib
import queue
import threading
from typing import Any, Dict, List, Optional


class _QueueWriter:
    """File-like object that enqueues each completed line as it is written."""

    def __init__(self, q: queue.Queue) -> None:
        self._q = q
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._q.put(line)
        return len(text)

    def flush(self) -> None:
        if self._buf:
            self._q.put(self._buf)
            self._buf = ""


class OrchestratorBridge:
    """Runs WorkflowOrchestrator.execute_workflow() in a daemon thread.

    Captures stdout line-by-line into a queue so the UI can stream progress
    without blocking Streamlit's event loop.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._log_queue: queue.Queue = queue.Queue()
        self._result: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None

    def start_workflow(self, workflow: dict, job_id: str, output_base: str) -> None:
        """Start execute_workflow() in a background daemon thread."""
        self._result = None
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            args=(workflow, job_id, output_base),
            daemon=True,
        )
        self._thread.start()

    def _run(self, workflow: dict, job_id: str, output_base: str) -> None:
        from services.llm_orchestrator.orchestrator import VERBOSITY_LOW, WorkflowOrchestrator

        writer = _QueueWriter(self._log_queue)
        try:
            with contextlib.redirect_stdout(writer):
                orchestrator = WorkflowOrchestrator(
                    output_base=output_base, verbosity=VERBOSITY_LOW
                )
                result = orchestrator.execute_workflow(workflow, job_id=job_id)
            self._result = result
        except Exception as exc:
            self._error = str(exc)
        finally:
            writer.flush()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_log_lines(self) -> List[str]:
        """Drain and return all lines queued since the last call."""
        lines: List[str] = []
        while not self._log_queue.empty():
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def get_result(self) -> Optional[Dict[str, Any]]:
        return self._result

    def get_error(self) -> Optional[str]:
        return self._error
