"""Installer for the docking platform.

Run this from inside a clone of this repo (or pass --compose-file to point
at one explicitly). The platform's own images (rcsb-downloader, ligand-
preparer, protein-preparer, moldina-runner, pymol-visualizer, pymol-
interactive, ui) are built locally from the Dockerfiles here — they are not
published to any registry — so a git clone is required; there is no
zero-dependency standalone download. Only the Ollama LLM image is pulled
from a public registry, and that happens automatically as part of
`docker compose up`.

Stdlib-only so it can be frozen into a single-file executable via PyInstaller
(see build.sh) without bundling any third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import List, Optional

HEALTH_URL = "http://localhost:3000/_stcore/health"
DOCKER_WINDOWS_URL = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
DOCKER_MAC_URL = "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
DOCKER_LINUX_SCRIPT_URL = "https://get.docker.com"


def detect_os() -> str:
    """Return 'windows', 'mac', or 'linux'."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "mac"
    return "linux"


def is_docker_installed() -> bool:
    """True if the `docker` CLI is on PATH and runs."""
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, check=False)
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def is_docker_running() -> bool:
    """True if the Docker daemon is reachable."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, check=False)
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _download_and_verify(
    url: str, dest: Path, *, min_size: int = 1024, expect_shebang: bool = False
) -> None:
    """Download url to dest and sanity-check it before it's ever executed.

    This can't pin a hash: Docker's own installers (get.docker.com, Docker
    Desktop) are versioned and change whenever Docker ships an update, so
    there's no stable checksum to verify against without the pin breaking
    future installs. What this does instead:
    - refuses anything but HTTPS (urllib validates the TLS cert by default)
    - confirms the download actually produced a plausible installer, not an
      empty file or an HTML error page from a captive portal/proxy
    - for shell scripts, checks for a `#!` shebang before running it as one
    - prints the SHA-256 of exactly what's about to run, so there's an
      audit trail in the installer's own output even though nothing here
      blocks execution on it
    """
    if not url.startswith("https://"):
        raise ValueError(f"Refusing to download over a non-HTTPS URL: {url}")

    urllib.request.urlretrieve(url, dest)

    size = dest.stat().st_size
    if size < min_size:
        raise RuntimeError(
            f"Downloaded file from {url} is only {size} bytes — looks corrupted "
            "or intercepted (e.g. a captive portal/proxy error page). Refusing to run it."
        )

    content = dest.read_bytes()
    if expect_shebang and not content.startswith(b"#!"):
        raise RuntimeError(
            f"Downloaded file from {url} doesn't start with a shebang — doesn't "
            "look like the expected shell script. Refusing to run it."
        )

    checksum = hashlib.sha256(content).hexdigest()
    print(f"  Downloaded {url}")
    print(f"  -> {dest} ({size:,} bytes, sha256={checksum})")


def install_docker(os_name: str) -> None:
    """Download and run the Docker Desktop / Engine installer for os_name.

    Windows/Mac use Docker Desktop's own installer (may prompt for admin
    rights). Linux uses Docker's official convenience script, which needs
    root — the user will be prompted for their sudo password in the terminal.
    """
    if os_name == "windows":
        installer_path = Path(tempfile.gettempdir()) / "DockerDesktopInstaller.exe"
        _download_and_verify(DOCKER_WINDOWS_URL, installer_path, min_size=1_000_000)
        subprocess.run([str(installer_path), "install", "--quiet", "--accept-license"], check=True)
    elif os_name == "mac":
        dmg_path = Path(tempfile.gettempdir()) / "Docker.dmg"
        _download_and_verify(DOCKER_MAC_URL, dmg_path, min_size=1_000_000)
        mount_point = "/Volumes/Docker"
        subprocess.run(["hdiutil", "attach", str(dmg_path), "-mountpoint", mount_point], check=True)
        try:
            subprocess.run(["cp", "-R", f"{mount_point}/Docker.app", "/Applications/"], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", mount_point], check=False)
    else:
        script_path = Path(tempfile.gettempdir()) / "get-docker.sh"
        _download_and_verify(DOCKER_LINUX_SCRIPT_URL, script_path, min_size=1024, expect_shebang=True)
        subprocess.run(["sh", str(script_path)], check=True)


def start_docker_daemon(os_name: str) -> None:
    """Launch the Docker Desktop app so its daemon starts (Windows/Mac only).

    Linux manages the Docker daemon via systemd, which should already be
    running as a service — nothing to launch here.
    """
    if os_name == "windows":
        docker_desktop = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Docker" / "Docker" / "Docker Desktop.exe"
        )
        if docker_desktop.exists():
            subprocess.Popen([str(docker_desktop)])
    elif os_name == "mac":
        subprocess.Popen(["open", "-a", "Docker"])


def build_images(compose_file: str) -> None:
    """Build the platform's own images from the Dockerfiles alongside
    compose_file (requires a git clone — there's nothing to pull them from)."""
    # Resolve to an absolute path *before* splitting it into the -f argument
    # and cwd: passing a relative -f path together with a cwd derived from it
    # would make docker re-resolve that same ".." against the new cwd and
    # land two directories up instead of one.
    resolved = str(Path(compose_file).resolve())
    subprocess.run(
        ["docker", "compose", "-f", resolved, "build"],
        check=True,
        cwd=str(Path(resolved).parent),
    )


def start_services(compose_file: str) -> None:
    # Pulls the Ollama image automatically (it has no build: section); the
    # platform's own services use the images built by build_images() above.
    resolved = str(Path(compose_file).resolve())
    subprocess.run(
        ["docker", "compose", "-f", resolved, "up", "-d"],
        check=True,
        cwd=str(Path(resolved).parent),
    )


def wait_for_health(url: str, timeout: float = 120, poll_interval: float = 2.0) -> bool:
    """Poll url until it returns HTTP 200, or timeout seconds elapse."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(poll_interval)
    return False


def open_browser(url: str) -> None:
    webbrowser.open(url)


_LAUNCHER_CONTENT = {
    "windows": (
        "docking-platform.bat",
        '@echo off\r\ncd /d "%~dp0"\r\ndocker compose up -d\r\nstart "" http://localhost:3000\r\n',
    ),
    "mac": (
        "docking-platform.command",
        '#!/bin/bash\ncd "$(dirname "$0")"\ndocker compose up -d\nopen http://localhost:3000\n',
    ),
    "linux": (
        "docking-platform.sh",
        '#!/bin/bash\ncd "$(dirname "$0")"\ndocker compose up -d\nxdg-open http://localhost:3000 2>/dev/null || true\n',
    ),
}


def write_launcher(os_name: str, target_dir: str = ".") -> Path:
    """Write a one-click launcher script for os_name into target_dir."""
    filename, content = _LAUNCHER_CONTENT[os_name]
    path = Path(target_dir) / filename
    path.write_text(content)
    if os_name in ("mac", "linux"):
        path.chmod(path.stat().st_mode | 0o111)
    return path


def _print_dry_run() -> None:
    print("[DRY RUN] Would check for Docker...")
    print("[DRY RUN] Would build platform images from docker-compose.yml...")
    print("[DRY RUN] Would start services...")
    print("[DRY RUN] Would open http://localhost:3000")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Docking platform installer — run from inside a clone of the repo."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print steps without doing anything")
    parser.add_argument(
        "--compose-file",
        default="docker-compose.yml",
        help="Path to docker-compose.yml (defaults to the one in the current directory)",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        _print_dry_run()
        return 0

    compose_path = Path(args.compose_file).resolve()
    if not compose_path.exists():
        print(f"  {compose_path} not found.")
        print("  Run this from inside a clone of the docking-platform repo, "
              "or pass --compose-file path/to/docker-compose.yml.")
        return 1
    compose_file = str(compose_path)
    install_dir = compose_path.parent

    os_name = detect_os()

    print("[1/5] Checking Docker...")
    if not is_docker_installed():
        print("  Docker not found. Installing...")
        install_docker(os_name)

    print("[2/5] Checking Docker is running...")
    if not is_docker_running():
        print("  Starting Docker...")
        start_docker_daemon(os_name)
        deadline = time.time() + 60
        while time.time() < deadline and not is_docker_running():
            time.sleep(2)
        if not is_docker_running():
            print("  Docker daemon did not start within 60s. Please start Docker manually and re-run.")
            return 1

    print("[3/5] Building platform images (first run ~5 min)...")
    try:
        build_images(compose_file)
    except subprocess.CalledProcessError as exc:
        print(f"  'docker compose build' failed: {exc}")
        print("  Make sure you're running this from inside a full clone of the repo "
              "(the Dockerfiles must be present alongside docker-compose.yml).")
        return 1

    print("[4/5] Starting services...")
    try:
        start_services(compose_file)
    except subprocess.CalledProcessError as exc:
        print(f"  'docker compose up' failed: {exc}")
        print("  Run 'docker compose logs' (or check for a port/container-name "
              "conflict with an existing stack) to debug.")
        return 1

    print("[5/5] Waiting for UI to be ready...")
    if wait_for_health(HEALTH_URL):
        open_browser("http://localhost:3000")
        write_launcher(os_name, target_dir=str(install_dir))
        print("Done! Platform is running at http://localhost:3000")
        return 0

    print("Timeout waiting for services. Run 'docker compose logs' to debug.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
