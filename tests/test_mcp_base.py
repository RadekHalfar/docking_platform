"""
Unit tests for MCP base server.
"""
import pytest
import json
import time
from common.mcp_base import MCPServer, MCPTool, MCPToolParameter, JSONRPCErrorCodes
from common.mcp_tooling import build_tool_parameters, register_mcp_tool


class MockMCPServer(MCPServer):
    """Mock implementation of MCP server for testing."""
    
    def setup_tools(self):
        """Setup test tools."""
        # Register a simple test tool
        def echo_handler(message: str) -> dict:
            return {"echo": message, "status": "ok"}
        
        echo_tool = MCPTool(
            name="echo",
            description="Echo back a message",
            parameters=[
                MCPToolParameter(
                    name="message",
                    type="string",
                    description="Message to echo",
                    required=True
                )
            ],
            handler=echo_handler,
            returns={"echo": "string", "status": "string"}
        )
        self.register_tool(echo_tool)


@pytest.fixture
def test_server():
    """Create a test MCP server instance."""
    server = MockMCPServer("test_server", port=9999)
    server.setup_tools()
    return server


@pytest.fixture
def test_client(test_server):
    """Create a test client for the server."""
    test_server.app.config['TESTING'] = True
    return test_server.app.test_client()


def test_health_endpoint(test_client):
    """Test health check endpoint."""
    response = test_client.get('/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "ok"
    assert data["service"] == "test_server"


def test_tools_list(test_client):
    """Test tools/list endpoint."""
    request_data = {
        "jsonrpc": "2.0",
        "id": "test_1",
        "method": "tools/list"
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == "test_1"
    assert "result" in data
    assert "tools" in data["result"]
    assert len(data["result"]["tools"]) == 1
    assert data["result"]["tools"][0]["name"] == "echo"


def test_tool_call_success(test_client):
    """Test successful tool call."""
    request_data = {
        "jsonrpc": "2.0",
        "id": "test_2",
        "method": "tool_call",
        "params": {
            "name": "echo",
            "arguments": {
                "message": "Hello, MCP!"
            }
        }
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == "test_2"
    assert "result" in data
    assert data["result"]["echo"] == "Hello, MCP!"
    assert data["result"]["status"] == "ok"


def test_tool_call_missing_parameter(test_client):
    """Test tool call with missing required parameter."""
    request_data = {
        "jsonrpc": "2.0",
        "id": "test_3",
        "method": "tool_call",
        "params": {
            "name": "echo",
            "arguments": {}
        }
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "error" in data
    assert data["error"]["code"] == JSONRPCErrorCodes.INVALID_PARAMS


def test_tool_call_unknown_tool(test_client):
    """Test tool call for non-existent tool."""
    request_data = {
        "jsonrpc": "2.0",
        "id": "test_4",
        "method": "tool_call",
        "params": {
            "name": "nonexistent_tool",
            "arguments": {}
        }
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "error" in data
    assert data["error"]["code"] == JSONRPCErrorCodes.METHOD_NOT_FOUND


def test_invalid_json_rpc_version(test_client):
    """Test request with invalid JSON-RPC version."""
    request_data = {
        "jsonrpc": "1.0",
        "id": "test_5",
        "method": "tools/list"
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data


def test_unknown_method(test_client):
    """Test request with unknown method."""
    request_data = {
        "jsonrpc": "2.0",
        "id": "test_6",
        "method": "unknown_method"
    }
    response = test_client.post(
        '/mcp',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 404
    data = json.loads(response.data)
    assert "error" in data
    assert data["error"]["code"] == JSONRPCErrorCodes.METHOD_NOT_FOUND


def test_mcp_tool_validation():
    """Test MCPTool argument validation."""
    tool = MCPTool(
        name="test_tool",
        description="Test tool",
        parameters=[
            MCPToolParameter(name="required_param", type="string", description="Required", required=True),
            MCPToolParameter(name="optional_param", type="int", description="Optional", required=False)
        ],
        handler=lambda **kwargs: kwargs
    )
    
    # Valid arguments
    valid, error = tool.validate_arguments({"required_param": "value"})
    assert valid is True
    assert error is None
    
    # Missing required parameter
    valid, error = tool.validate_arguments({"optional_param": 42})
    assert valid is False
    assert "required_param" in error


def test_build_tool_parameters_short_and_long_specs():
    """Test compact MCP parameter tuple formats."""
    parameters = build_tool_parameters([
        ("required_value", "string", "Required input", True),
        ("optional_value", "number", "Optional input"),
    ])

    assert len(parameters) == 2
    assert parameters[0].name == "required_value"
    assert parameters[0].required is True
    assert parameters[1].name == "optional_value"
    assert parameters[1].required is False


def test_tool_call_timeout_returns_error_without_blocking_forever(test_server, test_client):
    """A handler that runs longer than tool_timeout_seconds must produce a
    TIMEOUT_ERROR response instead of hanging the request indefinitely."""

    def slow_handler() -> dict:
        time.sleep(2)
        return {"status": "ok"}

    slow_tool = MCPTool(
        name="slow",
        description="A tool that never returns in time",
        parameters=[],
        handler=slow_handler,
    )
    test_server.register_tool(slow_tool)
    test_server.tool_timeout_seconds = 0.2

    request_data = {
        "jsonrpc": "2.0",
        "id": "timeout_test",
        "method": "tool_call",
        "params": {"name": "slow", "arguments": {}},
    }
    start = time.monotonic()
    response = test_client.post('/mcp', data=json.dumps(request_data), content_type='application/json')
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, "response must return at the timeout, not wait for the handler to finish"
    data = json.loads(response.data)
    assert "error" in data
    assert data["error"]["code"] == JSONRPCErrorCodes.TIMEOUT_ERROR
    assert "slow" in data["error"]["message"]
    assert "0.2" in data["error"]["message"]


def test_tool_call_within_timeout_still_succeeds(test_server, test_client):
    def quick_handler() -> dict:
        return {"status": "ok"}

    quick_tool = MCPTool(name="quick", description="Fast tool", parameters=[], handler=quick_handler)
    test_server.register_tool(quick_tool)
    test_server.tool_timeout_seconds = 5

    request_data = {
        "jsonrpc": "2.0",
        "id": "quick_test",
        "method": "tool_call",
        "params": {"name": "quick", "arguments": {}},
    }
    response = test_client.post('/mcp', data=json.dumps(request_data), content_type='application/json')
    data = json.loads(response.data)
    assert data["result"] == {"status": "ok"}


def test_run_serves_via_waitress_not_flask_dev_server(test_server, monkeypatch):
    """run() must use the production WSGI server (waitress), not Flask's
    app.run() dev server -- the dev server has no bound on concurrent
    request threads and isn't meant to serve real traffic."""
    import common.mcp_base as mcp_base_module

    calls = []
    monkeypatch.setattr(mcp_base_module, "WAITRESS_THREADS", 12)

    def _fake_serve(app, host, port, threads):
        calls.append({"app": app, "host": host, "port": port, "threads": threads})

    monkeypatch.setattr("waitress.serve", _fake_serve)

    test_server.host = "127.0.0.1"
    test_server.run()

    assert len(calls) == 1
    assert calls[0]["app"] is test_server.app
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9999
    assert calls[0]["threads"] == 12


def test_waitress_thread_pool_is_larger_than_tool_executor_pool():
    """waitress's pool must never be sized equal to (or smaller than) the
    tool-executor pool: every in-flight tool call occupies one thread in
    each pool at once, so equal sizing would let enough concurrent long
    tool calls starve /health of any free waitress thread. Regression test
    for that exact bug."""
    import common.mcp_base as mcp_base_module

    assert mcp_base_module.WAITRESS_THREADS > mcp_base_module.MCP_SERVER_THREADS
    assert mcp_base_module.WAITRESS_THREADS == (
        mcp_base_module.MCP_SERVER_THREADS + mcp_base_module.MCP_HEALTH_RESERVE_THREADS
    )


def test_register_mcp_tool_helper_registers_tool(test_server):
    """Test register_mcp_tool helper integrates with MCPServer."""

    def passthrough_handler(value: str) -> dict:
        return {"value": value}

    register_mcp_tool(
        test_server,
        name="passthrough",
        description="Simple passthrough tool",
        handler=passthrough_handler,
        parameters=build_tool_parameters([
            ("value", "string", "Value to return", True),
        ]),
        returns={"value": "string"},
    )

    assert "passthrough" in test_server.tools
    valid, error = test_server.tools["passthrough"].validate_arguments({"value": "x"})
    assert valid is True
    assert error is None
