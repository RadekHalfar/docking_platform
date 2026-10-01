"""Thread-safe wrapper for running execute_workflow() in the background."""
import contextlib
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.docking_stop import request_docking_stop


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
        self._cancel_event: threading.Event = threading.Event()
        self._job_dir: Optional[Path] = None

    def start_workflow(
        self,
        workflow: dict,
        job_id: str,
        output_base: str,
        services: Optional[List[str]] = None,
        job_name: str = "",
        state_manager: Optional[Any] = None,
    ) -> None:
        """Start execute_workflow() in a background daemon thread.

        Args:
            workflow: Validated workflow dict (services/parameters).
            job_id: Job identifier -- also the output directory name.
            output_base: Root directory for job outputs.
            services: Overrides ``workflow["services"]`` when given -- used
                for partial runs (e.g. the box-picker's "just prepare the
                receptor" phase) without needing a differently-shaped
                workflow dict from the caller.
            job_name: Optional user-supplied display label for the job.
            state_manager: Optional ``WorkflowStateManager`` for the active
                session -- threaded through to ``execute_workflow()`` so
                checkpoints/job.log entries are recorded the same way the
                CLI's interactive session does.
        """
        self._result = None
        self._error = None
        self._cancel_event = threading.Event()  # fresh event per run
        self._job_dir = Path(output_base) / job_id
        run_workflow = dict(workflow)
        if services is not None:
            run_workflow["services"] = services
        self._thread = threading.Thread(
            target=self._run,
            args=(run_workflow, job_id, output_base, job_name, state_manager, self._cancel_event),
            daemon=True,
        )
        self._thread.start()

    def _run(
        self,
        workflow: dict,
        job_id: str,
        output_base: str,
        job_name: str = "",
        state_manager: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        from services.llm_orchestrator.orchestrator import VERBOSITY_LOW, WorkflowOrchestrator

        writer = _QueueWriter(self._log_queue)
        try:
            with contextlib.redirect_stdout(writer):
                orchestrator = WorkflowOrchestrator(
                    output_base=output_base, verbosity=VERBOSITY_LOW
                )
                result = orchestrator.execute_workflow(
                    workflow,
                    job_id=job_id,
                    job_name=job_name,
                    state_manager=state_manager,
                    cancel_event=cancel_event,
                )
            self._result = result
        except Exception as exc:
            self._error = str(exc)
        finally:
            writer.flush()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> None:
        """Request cancellation of the in-progress run.

        Sets the cancel event, which stops the workflow before its next step
        (see ``WorkflowOrchestrator.execute_workflow``'s ``cancel_event``
        docstring), and writes the docking stop flag
        (``common/docking_stop.py``) so a dock step already in progress
        halts within about a second -- no further ligands start and running
        Moldina processes are killed -- instead of finishing every ligand.
        The flag is only written once the job's ``docking_results/`` exists;
        before that, the cancel event alone stops the run.
        """
        self._cancel_event.set()
        if self._job_dir is not None:
            request_docking_stop(self._job_dir / "docking_results")

    def stop_requested(self) -> bool:
        return self._cancel_event.is_set()

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
