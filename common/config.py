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
    "pymol_visualizer": 8005,
}

# Environment-variable names that override port defaults
SERVICE_PORT_ENV_VARS: dict[str, str] = {
    "rcsb_downloader": "RCSB_DOWNLOADER_PORT",
    "ligand_preparer": "LIGAND_PREPARER_PORT",
    "protein_preparer": "PROTEIN_PREPARER_PORT",
    "moldina_runner": "MOLDINA_RUNNER_PORT",
    "pymol_visualizer": "PYMOL_VISUALIZER_PORT",
}

# Shared cache-directory env var / default
CACHE_DIR_ENV = "CACHE_DIR"
CACHE_DIR_DEFAULT = "./cache"


def get_service_port(service_name: str) -> int:
    """Return the port for *service_name*, respecting env-var override."""
    env_var = SERVICE_PORT_ENV_VARS[service_name]
    default = SERVICE_DEFAULT_PORTS[service_name]
    return int(os.getenv(env_var, str(default)))


def get_cache_dir() -> str:
    """Return the cache directory path, respecting CACHE_DIR env override."""
    return os.getenv(CACHE_DIR_ENV, CACHE_DIR_DEFAULT)
