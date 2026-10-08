"""Thread-safe wrapper for running execute_workflow() in the background."""
import contextlib
import queue
import sys
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


# Which thread's stdout goes where (Phase 16 finding 1). redirect_stdout()
# swaps the process-wide sys.stdout, so two bridges running at once (the
# docking run and the box picker's receptor prep, or two browser tabs)
# captured each other's lines, and whichever finished first restored a
# stdout the other was still using. Instead one routing proxy is installed
# once and each bridge thread registers its own writer. The table is
# module-level, not on the proxy, so registrations survive a reinstall.
_stdout_routes: Dict[int, "_QueueWriter"] = {}
_stdout_routes_lock = threading.Lock()


class _ThreadRoutingStdout:
    """``sys.stdout`` replacement that writes to the calling thread's
    registered writer, else to the stdout it replaced (*fallback*).

    Output from threads a run itself starts goes to the fallback, not the
    run's log -- the in-process orchestrator prints only from the bridge
    thread (its MCP calls are synchronous HTTP; the thread pools live in
    the MCP servers).
    """

    def __init__(self, fallback) -> None:
        self._fallback = fallback

    def _target(self):
        return _stdout_routes.get(threading.get_ident(), self._fallback)

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    def __getattr__(self, name: str):
        # encoding, isatty(), fileno(), ... -- describe the real stream.
        return getattr(self._fallback, name)


def _install_stdout_router() -> None:
    """Make sure ``sys.stdout`` is the routing proxy. Idempotent; installs
    again over whatever replaced it since (e.g. pytest's capture)."""
    with _stdout_routes_lock:
        if not isinstance(sys.stdout, _ThreadRoutingStdout):
            sys.stdout = _ThreadRoutingStdout(sys.stdout)


@contextlib.contextmanager
def _route_stdout_of_this_thread(writer: "_QueueWriter"):
    """Send this thread's ``print()`` output to *writer* for the block."""
    _install_stdout_router()
    ident = threading.get_ident()
    with _stdout_routes_lock:
        _stdout_routes[ident] = writer
    try:
        yield
    finally:
        with _stdout_routes_lock:
            _stdout_routes.pop(ident, None)


class OrchestratorBridge:
    """Runs WorkflowOrchestrator.execute_workflow() in a daemon thread.

    Captures its own thread's stdout line-by-line into a queue (see
    ``_route_stdout_of_this_thread``) so the UI can stream progress without
    blocking the GUI, while other bridges' output stays out of it.
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
            with _route_stdout_of_this_thread(writer):
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
