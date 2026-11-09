"""Shared CLI/env bootstrap for MCP service `server.py` `main()` entry points.

Every service resolves host/port/cache-dir the same way: an explicit CLI
flag wins if given, otherwise fall back to the env-var-backed default from
common.config. Keeping this in one place means the --host/--port flags the
services' Dockerfiles pass are honoured by every service the same way.
"""
import argparse
from typing import Optional

from common.config import get_cache_dir, get_service_port


def build_service_arg_parser(
    service_name: str,
    description: str,
    cache_dir_help: str = "Cache directory (default from CACHE_DIR env or ./cache)",
) -> argparse.ArgumentParser:
    """Return a parser pre-populated with the --host/--port/--cache-dir
    flags every service supports identically. Callers needing extra flags
    (e.g. moldina_runner's --moldina-path) can add them before parsing.

    cache_dir_help is overridable because moldina_runner's actual --cache-dir
    default (~/.docking_cache/moldina, applied by MoldinaRunnerServer itself)
    differs from every other service's (CACHE_DIR env / ./cache).
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Server port (default: {get_service_port(service_name)}, "
             f"overridable via {service_name.upper()}_PORT or this flag)",
    )
    parser.add_argument("--cache-dir", default=None, help=cache_dir_help)
    return parser


def resolve_port(service_name: str, cli_port: Optional[int]) -> int:
    """--port wins if given; otherwise the service's env-var-backed default."""
    return cli_port if cli_port is not None else get_service_port(service_name)


def resolve_cache_dir(cli_cache_dir: Optional[str]) -> str:
    """--cache-dir wins if given; otherwise CACHE_DIR env var / ./cache."""
    return cli_cache_dir if cli_cache_dir is not None else get_cache_dir()
