"""Tests for common.config — port/cache-dir defaults and env-var overrides."""
import pytest
from common.config import (
    CACHE_DIR_DEFAULT,
    SERVICE_DEFAULT_PORTS,
    SERVICE_PORT_ENV_VARS,
    get_cache_dir,
    get_service_port,
)

ALL_SERVICES = list(SERVICE_DEFAULT_PORTS.keys())


class TestGetServicePort:
    def test_returns_default_when_env_unset(self, monkeypatch):
        for service, default_port in SERVICE_DEFAULT_PORTS.items():
            env_var = SERVICE_PORT_ENV_VARS[service]
            monkeypatch.delenv(env_var, raising=False)
            assert get_service_port(service) == default_port

    def test_respects_env_override(self, monkeypatch):
        service = "rcsb_downloader"
        env_var = SERVICE_PORT_ENV_VARS[service]
        monkeypatch.setenv(env_var, "9999")
        assert get_service_port(service) == 9999

    def test_env_override_is_int(self, monkeypatch):
        service = "ligand_preparer"
        env_var = SERVICE_PORT_ENV_VARS[service]
        monkeypatch.setenv(env_var, "12345")
        result = get_service_port(service)
        assert isinstance(result, int)
        assert result == 12345

    def test_raises_on_unknown_service(self):
        with pytest.raises(KeyError):
            get_service_port("nonexistent_service")

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_default_ports_are_positive(self, service):
        assert SERVICE_DEFAULT_PORTS[service] > 0

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_each_service_has_port_env_var(self, service):
        assert service in SERVICE_PORT_ENV_VARS


class TestGetCacheDir:
    def test_returns_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CACHE_DIR", raising=False)
        assert get_cache_dir() == CACHE_DIR_DEFAULT

    def test_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("CACHE_DIR", "/tmp/my_cache")
        assert get_cache_dir() == "/tmp/my_cache"

    def test_default_is_relative_path(self):
        assert not CACHE_DIR_DEFAULT.startswith("/")
