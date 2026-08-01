"""Tests for the shared MCP service CLI/env bootstrap helper."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from common.mcp_bootstrap import build_service_arg_parser, resolve_cache_dir, resolve_port


class TestBuildServiceArgParser:
    def test_default_flags_present(self):
        parser = build_service_arg_parser("rcsb_downloader", "Test service")
        args = parser.parse_args([])
        assert args.host == "0.0.0.0"
        assert args.port is None
        assert args.cache_dir is None

    def test_cli_flags_override_defaults(self):
        parser = build_service_arg_parser("rcsb_downloader", "Test service")
        args = parser.parse_args(["--host", "127.0.0.1", "--port", "9999", "--cache-dir", "/tmp/x"])
        assert args.host == "127.0.0.1"
        assert args.port == 9999
        assert args.cache_dir == "/tmp/x"

    def test_include_cache_dir_false_omits_flag(self):
        parser = build_service_arg_parser("pymol_visualizer", "Test service", include_cache_dir=False)
        assert "cache_dir" not in parser.parse_args(["--host", "x"]).__dict__

        # --cache-dir should be unrecognized when omitted
        with patch("sys.stderr"), pytest.raises(SystemExit):
            parser.parse_args(["--cache-dir", "/tmp/x"])

    def test_extra_arguments_can_be_added(self):
        """moldina_runner adds --moldina-path/--wsl-distro on top of the shared set."""
        parser = build_service_arg_parser("moldina_runner", "Test service")
        parser.add_argument("--moldina-path", dest="moldina_path", default=None)
        args = parser.parse_args(["--moldina-path", "/usr/local/bin/moldina"])
        assert args.moldina_path == "/usr/local/bin/moldina"
        assert args.port is None  # shared flags still present


class TestResolvePort:
    def test_cli_port_wins_when_given(self):
        assert resolve_port("rcsb_downloader", 1234) == 1234

    def test_falls_back_to_env_backed_default_when_none(self):
        with patch("common.mcp_bootstrap.get_service_port", return_value=8001) as mock_get:
            assert resolve_port("rcsb_downloader", None) == 8001
            mock_get.assert_called_once_with("rcsb_downloader")

    def test_cli_port_zero_is_respected_not_treated_as_falsy(self):
        # 0 is falsy in Python but a legitimate (if unusual) explicit value --
        # must not be silently replaced by the env-backed default.
        assert resolve_port("rcsb_downloader", 0) == 0


class TestResolveCacheDir:
    def test_cli_value_wins_when_given(self):
        assert resolve_cache_dir("/custom/cache") == "/custom/cache"

    def test_falls_back_to_env_backed_default_when_none(self):
        with patch("common.mcp_bootstrap.get_cache_dir", return_value="./cache") as mock_get:
            assert resolve_cache_dir(None) == "./cache"
            mock_get.assert_called_once()

    def test_cli_value_empty_string_is_respected_not_treated_as_falsy(self):
        assert resolve_cache_dir("") == ""
