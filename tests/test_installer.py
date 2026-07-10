"""Tests for installer/install.py (stdlib-only installer, run from a repo clone).

install_docker/start_docker_daemon are intentionally not exercised here —
they download and run real system installers, which is out of scope for a
unit test and never something to run against the machine running pytest.
"""
import os
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from installer import install


class TestDetectOs:
    @patch("installer.install.platform.system")
    def test_windows(self, mock_system):
        mock_system.return_value = "Windows"
        assert install.detect_os() == "windows"

    @patch("installer.install.platform.system")
    def test_mac(self, mock_system):
        mock_system.return_value = "Darwin"
        assert install.detect_os() == "mac"

    @patch("installer.install.platform.system")
    def test_linux(self, mock_system):
        mock_system.return_value = "Linux"
        assert install.detect_os() == "linux"

    def test_returns_valid_string(self):
        assert install.detect_os() in ("windows", "mac", "linux")


class TestIsDockerInstalled:
    @patch("installer.install.subprocess.run")
    def test_docker_installed_returns_true(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        assert install.is_docker_installed() is True

    @patch("installer.install.subprocess.run")
    def test_docker_not_found_returns_false(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert install.is_docker_installed() is False

    @patch("installer.install.subprocess.run")
    def test_docker_nonzero_exit_returns_false(self, mock_run):
        mock_run.return_value = Mock(returncode=1)
        assert install.is_docker_installed() is False


class TestIsDockerRunning:
    @patch("installer.install.subprocess.run")
    def test_running_returns_true(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        assert install.is_docker_running() is True

    @patch("installer.install.subprocess.run")
    def test_not_running_returns_false(self, mock_run):
        mock_run.return_value = Mock(returncode=1)
        assert install.is_docker_running() is False

    @patch("installer.install.subprocess.run")
    def test_docker_not_found_returns_false(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert install.is_docker_running() is False


class TestWaitForHealth:
    @patch("installer.install.urllib.request.urlopen")
    def test_success_on_first_try(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = Mock(return_value=Mock(status=200))
        mock_urlopen.return_value.__exit__ = Mock(return_value=False)
        assert install.wait_for_health("http://x", timeout=5, poll_interval=0.01) is True

    @patch("installer.install.time.sleep", return_value=None)
    @patch("installer.install.urllib.request.urlopen")
    def test_timeout_returns_false(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        assert install.wait_for_health("http://x", timeout=0.05, poll_interval=0.01) is False

    @patch("installer.install.time.sleep", return_value=None)
    @patch("installer.install.urllib.request.urlopen")
    def test_non_200_status_keeps_polling_until_timeout(self, mock_urlopen, mock_sleep):
        mock_urlopen.return_value.__enter__ = Mock(return_value=Mock(status=503))
        mock_urlopen.return_value.__exit__ = Mock(return_value=False)
        assert install.wait_for_health("http://x", timeout=0.05, poll_interval=0.01) is False


class TestBuildAndStartServices:
    @patch("installer.install.subprocess.run")
    def test_build_images_calls_compose_build_in_compose_files_dir(self, mock_run, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}")
        resolved = str(compose_file.resolve())

        install.build_images(str(compose_file))

        mock_run.assert_called_once_with(
            ["docker", "compose", "-f", resolved, "build"],
            check=True,
            cwd=str(Path(resolved).parent),
        )

    @patch("installer.install.subprocess.run")
    def test_start_services_calls_compose_up_in_compose_files_dir(self, mock_run, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}")
        resolved = str(compose_file.resolve())

        install.start_services(str(compose_file))

        mock_run.assert_called_once_with(
            ["docker", "compose", "-f", resolved, "up", "-d"],
            check=True,
            cwd=str(Path(resolved).parent),
        )

    @patch("installer.install.subprocess.run")
    def test_relative_compose_file_does_not_double_apply_dotdot(self, mock_run, tmp_path, monkeypatch):
        # Regression test: a relative "-f ../docker-compose.yml" combined with
        # a cwd naively derived from that same relative path used to resolve
        # two directories up instead of one.
        repo_dir = tmp_path / "repo"
        subdir = repo_dir / "dist"
        subdir.mkdir(parents=True)
        compose_file = repo_dir / "docker-compose.yml"
        compose_file.write_text("services: {}")
        monkeypatch.chdir(subdir)

        install.build_images("../docker-compose.yml")

        called_args, called_kwargs = mock_run.call_args
        assert called_kwargs["cwd"] == str(repo_dir.resolve())
        assert called_args[0][3] == str(compose_file.resolve())


class TestWriteLauncher:
    def test_writes_windows_launcher(self, tmp_path):
        path = install.write_launcher("windows", target_dir=str(tmp_path))
        assert path.exists()
        assert path.name == "docking-platform.bat"
        assert "docker compose up -d" in path.read_text()

    def test_writes_mac_launcher_executable(self, tmp_path):
        path = install.write_launcher("mac", target_dir=str(tmp_path))
        assert path.exists()
        assert os.access(path, os.X_OK)

    def test_writes_linux_launcher_executable(self, tmp_path):
        path = install.write_launcher("linux", target_dir=str(tmp_path))
        assert path.exists()
        assert os.access(path, os.X_OK)


class TestMainRequiresComposeFile:
    def test_missing_compose_file_prints_helpful_message_and_exits_nonzero(self, tmp_path, capsys):
        rc = install.main(["--compose-file", str(tmp_path / "does-not-exist.yml")])
        out = capsys.readouterr().out

        assert rc == 1
        assert "not found" in out
        assert "clone" in out


class TestMainBuildAndStartFailures:
    @patch("installer.install.build_images")
    @patch("installer.install.is_docker_running", return_value=True)
    @patch("installer.install.is_docker_installed", return_value=True)
    def test_build_failure_prints_clean_message_and_exits_nonzero(
        self, mock_installed, mock_running, mock_build, tmp_path, capsys
    ):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}")
        mock_build.side_effect = subprocess.CalledProcessError(1, ["docker", "compose", "build"])

        rc = install.main(["--compose-file", str(compose_file)])
        out = capsys.readouterr().out

        assert rc == 1
        assert "'docker compose build' failed" in out
        assert "Traceback" not in out

    @patch("installer.install.start_services")
    @patch("installer.install.build_images")
    @patch("installer.install.is_docker_running", return_value=True)
    @patch("installer.install.is_docker_installed", return_value=True)
    def test_start_services_failure_prints_clean_message_and_exits_nonzero(
        self, mock_installed, mock_running, mock_build, mock_start, tmp_path, capsys
    ):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}")
        mock_start.side_effect = subprocess.CalledProcessError(1, ["docker", "compose", "up", "-d"])

        rc = install.main(["--compose-file", str(compose_file)])
        out = capsys.readouterr().out

        assert rc == 1
        assert "'docker compose up' failed" in out
        assert "Traceback" not in out

    @patch("installer.install.write_launcher")
    @patch("installer.install.open_browser")
    @patch("installer.install.wait_for_health", return_value=True)
    @patch("installer.install.start_services")
    @patch("installer.install.build_images")
    @patch("installer.install.is_docker_running", return_value=True)
    @patch("installer.install.is_docker_installed", return_value=True)
    def test_success_path_calls_steps_in_order_and_returns_zero(
        self,
        mock_installed,
        mock_running,
        mock_build,
        mock_start,
        mock_wait,
        mock_open_browser,
        mock_write_launcher,
        tmp_path,
    ):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}")

        rc = install.main(["--compose-file", str(compose_file)])

        assert rc == 0
        mock_build.assert_called_once_with(str(compose_file))
        mock_start.assert_called_once_with(str(compose_file))
        mock_open_browser.assert_called_once_with("http://localhost:3000")
        mock_write_launcher.assert_called_once_with(install.detect_os(), target_dir=str(tmp_path))


class TestDryRun:
    def test_dry_run_prints_expected_steps(self, capsys):
        rc = install.main(["--dry-run"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "[DRY RUN] Would check for Docker...\n" in out
        assert "[DRY RUN] Would build platform images from docker-compose.yml...\n" in out
        assert "[DRY RUN] Would start services...\n" in out
        assert "[DRY RUN] Would open http://localhost:3000\n" in out

    @patch("installer.install.subprocess.run")
    @patch("installer.install.urllib.request.urlretrieve")
    @patch("installer.install.urllib.request.urlopen")
    def test_dry_run_touches_neither_subprocess_nor_network(
        self, mock_urlopen, mock_urlretrieve, mock_run
    ):
        install.main(["--dry-run"])
        mock_run.assert_not_called()
        mock_urlretrieve.assert_not_called()
        mock_urlopen.assert_not_called()
