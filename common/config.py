"""
Shared runtime configuration defaults for MCP service servers.

Each service reads its port from an environment variable whose name is
listed in SERVICE_PORT_ENV_VARS, falling back to SERVICE_DEFAULT_PORTS.
The cache directory is shared across all services via CACHE_DIR_ENV.
"""
import os

# Default port numbers per service
SERVICE_DEFAULT_PORTS: dict[str, int] = {
    "rcsb_downloader": 8001,
    "ligand_preparer": 8002,
    "protein_preparer": 8003,
    "moldina_runner": 8004,
}

# Environment-variable names that override port defaults
SERVICE_PORT_ENV_VARS: dict[str, str] = {
    "rcsb_downloader": "RCSB_DOWNLOADER_PORT",
    "ligand_preparer": "LIGAND_PREPARER_PORT",
    "protein_preparer": "PROTEIN_PREPARER_PORT",
    "moldina_runner": "MOLDINA_RUNNER_PORT",
}

# Shared cache-directory env var / default
CACHE_DIR_ENV = "CACHE_DIR"
CACHE_DIR_DEFAULT = "./cache"

# Per-tool-call timeout, shared by every MCP service (see common/mcp_base.py).
# Was already documented in .env.example but never actually read anywhere.
MAX_TOOL_RUNTIME_ENV = "MAX_JOB_RUNTIME_SECONDS"
MAX_TOOL_RUNTIME_DEFAULT = 3600


def get_service_port(service_name: str) -> int:
    """Return the port for *service_name*, respecting env-var override."""
    env_var = SERVICE_PORT_ENV_VARS[service_name]
    default = SERVICE_DEFAULT_PORTS[service_name]
    raw = os.getenv(env_var, str(default))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Invalid port value for {env_var}: {raw!r}") from None


def get_cache_dir() -> str:
    """Return the cache directory path, respecting CACHE_DIR env override."""
    return os.getenv(CACHE_DIR_ENV, CACHE_DIR_DEFAULT)


def get_max_tool_runtime_seconds() -> int:
    """Return the per-tool-call timeout, respecting MAX_JOB_RUNTIME_SECONDS override."""
    raw = os.getenv(MAX_TOOL_RUNTIME_ENV, str(MAX_TOOL_RUNTIME_DEFAULT))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Invalid value for {MAX_TOOL_RUNTIME_ENV}: {raw!r}") from None
