"""Tests for the OrchestratorBridge background-thread wrapper."""
import threading
import time

import pytest
from unittest.mock import MagicMock, patch

from ui.orchestrator_bridge import OrchestratorBridge, _QueueWriter


class TestQueueWriter:
    def test_lines_flushed_to_queue_on_newline(self):
        import queue
        q = queue.Queue()
        w = _QueueWriter(q)
        w.write("hello\nworld\n")
        assert q.get_nowait() == "hello"
        assert q.get_nowait() == "world"

    def test_partial_line_held_until_flush(self):
        import queue
        q = queue.Queue()
        w = _QueueWriter(q)
        w.write("no newline yet")
        assert q.empty()
        w.flush()
        assert q.get_nowait() == "no newline yet"

    def test_write_returns_length(self):
        import queue
        w = _QueueWriter(queue.Queue())
        assert w.write("abc\n") == 4


class TestOrchestratorBridge:
    def test_not_running_before_start(self):
        bridge = OrchestratorBridge()
        assert bridge.is_running() is False

    def test_result_and_error_none_before_start(self):
        bridge = OrchestratorBridge()
        assert bridge.get_result() is None
        assert bridge.get_error() is None

    def test_get_log_lines_empty_before_start(self):
        bridge = OrchestratorBridge()
        assert bridge.get_log_lines() == []

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_is_running_after_start(self, MockOrchestrator):
        started = threading.Event()
        released = threading.Event()

        def fake_execute(*args, **kwargs):
            started.set()
            released.wait(timeout=3)
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "job_test", "/tmp")
        started.wait(timeout=3)

        assert bridge.is_running() is True
        released.set()

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_captures_print_output(self, MockOrchestrator):
        def fake_execute(*args, **kwargs):
            print("step 1 done")
            print("step 2 done")
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "job_test", "/tmp")
        bridge._thread.join(timeout=5)

        lines = bridge.get_log_lines()
        assert "step 1 done" in lines
        assert "step 2 done" in lines

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_result_available_after_completion(self, MockOrchestrator):
        mock_instance = MagicMock()
        mock_instance.execute_workflow.return_value = {"status": "completed", "job_id": "j1"}
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "j1", "/tmp")
        bridge._thread.join(timeout=5)

        assert bridge.get_result() == {"status": "completed", "job_id": "j1"}
        assert bridge.get_error() is None
        assert bridge.is_running() is False

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_stores_error_on_exception(self, MockOrchestrator):
        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = RuntimeError("docking failed")
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "j1", "/tmp")
        bridge._thread.join(timeout=5)

        assert bridge.get_error() == "docking failed"
        assert bridge.get_result() is None

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_services_override_replaces_workflow_services(self, MockOrchestrator):
        """The box-picker's partial run passes services= explicitly, which
        must override workflow["services"] rather than being ignored."""
        captured = {}

        def fake_execute(workflow, job_id=None, **kwargs):
            captured["workflow"] = workflow
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        workflow = {"services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"], "parameters": {}}
        bridge.start_workflow(workflow, "job_test", "/tmp", services=["get_input_files", "prepare_protein"])
        bridge._thread.join(timeout=5)

        assert captured["workflow"]["services"] == ["get_input_files", "prepare_protein"]
        # Original dict passed by the caller must not be mutated in place.
        assert workflow["services"] == ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_no_services_override_keeps_workflow_services(self, MockOrchestrator):
        captured = {}

        def fake_execute(workflow, job_id=None, **kwargs):
            captured["workflow"] = workflow
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        workflow = {"services": ["get_input_files"], "parameters": {}}
        bridge.start_workflow(workflow, "job_test", "/tmp")
        bridge._thread.join(timeout=5)

        assert captured["workflow"]["services"] == ["get_input_files"]

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_job_name_passed_through_to_execute_workflow(self, MockOrchestrator):
        captured = {}

        def fake_execute(workflow, job_id=None, job_name=None, **kwargs):
            captured["job_name"] = job_name
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "job_test", "/tmp", job_name="Overnight batch")
        bridge._thread.join(timeout=5)

        assert captured["job_name"] == "Overnight batch"

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_job_name_defaults_to_empty_string(self, MockOrchestrator):
        captured = {}

        def fake_execute(workflow, job_id=None, job_name=None, **kwargs):
            captured["job_name"] = job_name
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "job_test", "/tmp")
        bridge._thread.join(timeout=5)

        assert captured["job_name"] == ""

    @patch("services.llm_orchestrator.orchestrator.WorkflowOrchestrator")
    def test_second_start_resets_previous_result(self, MockOrchestrator):
        started = threading.Event()
        released = threading.Event()

        call_count = 0

        def fake_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                started.set()
                released.wait(timeout=3)
            return {"status": "completed"}

        mock_instance = MagicMock()
        mock_instance.execute_workflow.side_effect = fake_execute
        MockOrchestrator.return_value = mock_instance

        bridge = OrchestratorBridge()
        bridge.start_workflow({}, "j1", "/tmp")
        bridge._thread.join(timeout=5)
        assert bridge.get_result() is not None

        # Start second run — blocks inside fake_execute until we check
        bridge.start_workflow({}, "j2", "/tmp")
        started.wait(timeout=3)
        # While second run is in progress, result should still be None (was cleared on start)
        assert bridge.get_result() is None
        released.set()
