"""
Base MCP Server implementation.

Provides common functionality for all MCP services:
- JSON-RPC 2.0 message handling
- Tool registration and discovery
- HTTP/WebSocket endpoints
- Error handling
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional, Callable
from flask import Flask, request, jsonify
from common.config import get_max_tool_runtime_seconds
from common.schemas import MCPToolDefinition, MCPToolParameter

logger = logging.getLogger(__name__)

# Bounded worker pool for the production WSGI server (see MCPServer.run()).
# A handful of ligands can be docked/prepared concurrently while still
# leaving room for /health polls; override via env if a deployment needs more.
MCP_SERVER_THREADS = int(os.environ.get("MCP_SERVER_THREADS", "8"))


class MCPTool:
    """Represents a single MCP tool with its metadata and handler."""
    
    def __init__(
        self,
        name: str,
        description: str,
        parameters: List[MCPToolParameter],
        handler: Callable,
        returns: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.returns = returns or {}
    
    def to_definition(self) -> MCPToolDefinition:
        """Convert to MCP tool definition for tools/list."""
        return MCPToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[p.model_dump() for p in self.parameters],
            returns=self.returns
        )
    
    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate tool arguments against parameter definitions."""
        for param in self.parameters:
            if param.required and param.name not in arguments:
                return False, f"Missing required parameter: {param.name}"
        return True, None


class MCPServer(ABC):
    """
    Base class for MCP servers.
    
    Provides JSON-RPC 2.0 endpoints and tool registration.
    Each service should inherit from this and register its tools.
    """
    
    def __init__(self, name: str, port: int, host: str = "0.0.0.0"):
        self.name = name
        self.port = port
        self.host = host
        self.tools: Dict[str, MCPTool] = {}
        self.tool_timeout_seconds = get_max_tool_runtime_seconds()

        # Runs tool handlers off the request-handling thread so a call that
        # exceeds tool_timeout_seconds can be answered with a timeout error
        # instead of leaving the HTTP client hanging indefinitely. This does
        # NOT cancel the handler itself -- Python can't safely force-kill a
        # running thread -- so a runaway handler keeps consuming a worker
        # slot in the background until it finishes on its own; the
        # subprocess-level timeouts inside individual handlers (e.g.
        # moldina_runner's own 3600s subprocess.run timeout) are what
        # actually bound that.
        self._tool_executor = ThreadPoolExecutor(
            max_workers=MCP_SERVER_THREADS, thread_name_prefix=f"{name}-tool"
        )

        # Setup Flask app
        self.app = Flask(name)
        self._setup_routes()
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format=f'[{name}] %(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(name)
    
    def _setup_routes(self):
        """Setup Flask routes for MCP endpoints."""
        
        @self.app.route('/health', methods=['GET'])
        def health():
            """Health check endpoint."""
            return jsonify({"status": "ok", "service": self.name})
        
        @self.app.route('/mcp', methods=['POST'])
        def mcp_endpoint():
            """Main MCP JSON-RPC endpoint."""
            try:
                data = request.get_json()
                if not data:
                    return jsonify(self._error_response(
                        None, -32700, "Parse error: Invalid JSON"
                    )), 400
                
                # Validate JSON-RPC 2.0 format
                if data.get('jsonrpc') != '2.0':
                    return jsonify(self._error_response(
                        data.get('id'), -32600, "Invalid Request: jsonrpc must be '2.0'"
                    )), 400
                
                method = data.get('method')
                request_id = data.get('id')
                
                if method == 'tools/list':
                    return jsonify(self._handle_tools_list(request_id))
                elif method == 'tool_call':
                    return jsonify(self._handle_tool_call(request_id, data.get('params', {})))
                else:
                    return jsonify(self._error_response(
                        request_id, -32601, f"Method not found: {method}"
                    )), 404
                    
            except Exception as e:
                self.logger.error(f"Error handling request: {e}", exc_info=True)
                return jsonify(self._error_response(
                    None, -32603, f"Internal error: {str(e)}"
                )), 500
    
    def _handle_tools_list(self, request_id: str) -> Dict[str, Any]:
        """Handle tools/list request."""
        tools = [tool.to_definition().model_dump() for tool in self.tools.values()]
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": tools
            }
        }
    
    def _handle_tool_call(self, request_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool_call request."""
        tool_name = params.get('name')
        arguments = params.get('arguments', {})
        
        if not tool_name:
            return self._error_response(request_id, -32602, "Missing 'name' in params")
        
        if tool_name not in self.tools:
            return self._error_response(
                request_id, -32601, f"Tool not found: {tool_name}"
            )
        
        tool = self.tools[tool_name]
        
        # Validate arguments
        valid, error_msg = tool.validate_arguments(arguments)
        if not valid:
            return self._error_response(request_id, -32602, error_msg)
        
        # Execute tool off-thread so a call that hangs past tool_timeout_seconds
        # can be answered instead of leaving the client waiting indefinitely.
        try:
            self.logger.info(f"Executing tool: {tool_name} with args: {arguments}")
            future = self._tool_executor.submit(tool.handler, **arguments)
            try:
                result = future.result(timeout=self.tool_timeout_seconds)
            except FutureTimeoutError:
                self.logger.error(
                    f"Tool '{tool_name}' timed out after {self.tool_timeout_seconds}s "
                    "(handler keeps running in the background; this response is a timeout, not a cancellation)"
                )
                return self._error_response(
                    request_id, JSONRPCErrorCodes.TIMEOUT_ERROR,
                    f"Tool '{tool_name}' timed out after {self.tool_timeout_seconds}s",
                )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
        except Exception as e:
            self.logger.error(f"Tool execution error: {e}", exc_info=True)
            return self._error_response(
                request_id, -32000, f"Tool execution failed: {str(e)}"
            )
    
    def _error_response(self, request_id: Optional[str], code: int, message: str) -> Dict[str, Any]:
        """Create JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message
            }
        }
    
    def register_tool(self, tool: MCPTool):
        """Register a tool with this server."""
        self.tools[tool.name] = tool
        self.logger.info(f"Registered tool: {tool.name}")
    
    @abstractmethod
    def setup_tools(self):
        """
        Setup and register all tools for this service.
        Must be implemented by subclasses.
        """
        pass
    
    def run(self):
        """Start the MCP server."""
        self.setup_tools()
        self.logger.info(f"Starting {self.name} on {self.host}:{self.port}")
        self.logger.info(f"Registered tools: {list(self.tools.keys())}")
        # Flask's own app.run() is a development server not meant to serve
        # real traffic (single-process, and threaded=True spawns one thread
        # per request with no upper bound). waitress is a production-grade,
        # pure-Python/cross-platform WSGI server with a bounded thread pool —
        # sized so /health can still be served promptly alongside a few
        # long-running tool calls (e.g. docking a large batch) without
        # accepting unlimited concurrent requests.
        from waitress import serve

        serve(self.app, host=self.host, port=self.port, threads=MCP_SERVER_THREADS)


# Standard JSON-RPC error codes
class JSONRPCErrorCodes:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Custom application errors start at -32000
    TOOL_EXECUTION_ERROR = -32000
    VALIDATION_ERROR = -32001
    RESOURCE_NOT_FOUND = -32002
    TIMEOUT_ERROR = -32003
