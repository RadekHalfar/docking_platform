# Docker Deployment Guide

## MCP Docking System - Containerized Deployment

This guide explains how to deploy the MCP Docking System with Docker containers.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Architecture Overview](#architecture-overview)
4. [Building Images](#building-images)
5. [Running Services](#running-services)
6. [Configuration](#configuration)
7. [Volumes and Data Persistence](#volumes-and-data-persistence)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)
10. [Advanced Usage](#advanced-usage)
11. [Command Cheat Sheet](#command-cheat-sheet)

---

## Prerequisites

### Required Software

- **Docker**: Version 20.10 or higher
  - [Install Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac)
  - [Install Docker Engine](https://docs.docker.com/engine/install/) (Linux)

- **Docker Compose**: Version 2.0 or higher
  - Included with Docker Desktop
  - Install separately on Linux: `apt-get install docker-compose-plugin`

### System Requirements

- **Minimum**:
  - 4 CPU cores
  - 8 GB RAM
  - 20 GB disk space

- **Recommended**:
  - 8+ CPU cores
  - 16+ GB RAM
  - 50+ GB disk space (for caching structures)

---

## Quick Start

### 1. Build All Images

```powershell
# Windows PowerShell
.\docker_build.ps1

# Or using docker compose directly
docker compose build
```

```bash
# Linux/Mac
./docker_build.sh

# Or using docker compose directly
docker compose build
```

### 2. Start All Services

```bash
docker compose up -d
```

### 3. Verify Services are Running

```bash
docker compose ps
```

Expected output:
```
NAME                    IMAGE                       STATUS
mcp-rcsb-downloader     mcp-rcsb-downloader:latest   Up (healthy)
mcp-ligand-preparer     mcp-ligand-preparer:latest   Up (healthy)
mcp-protein-preparer    mcp-protein-preparer:latest  Up (healthy)
mcp-moldina-runner      mcp-moldina-runner:latest    Up (healthy)
mcp-ui                  mcp-docking-ui:latest        Up (healthy)
```

`mcp-llm` (Ollama) is not in the default stack; see
[Docker Compose Profiles](#docker-compose-profiles).

### 4. Test a Service

```bash
curl http://localhost:8001/health
```

Expected response:
```json
{"service": "rcsb_downloader", "status": "ok"}
```

### 5. Run Workflow Without Local Python Environment

Use the dev profile script runner. The workspace is bind-mounted into the container,
so script edits on host are picked up immediately (no image rebuild needed).

```powershell
# Check orchestration health from inside container network
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# Run full natural-language workflow (needs an LLM; none is configured by
# default -- here the opt-in local Ollama, see "Choose an LLM" in the README)
docker compose --profile local-llm up -d ollama
docker exec -it mcp-llm ollama pull gemma3:4b
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-provider ollama --llm-model gemma3:4b "Download 6lu7 and ATP, prepare and dock"
```

Without an LLM, natural-language commands print a setup message and exit 1;
dock from the GUI's forms instead.

If docking coordinates are missing, the CLI asks for `center`/`box_size` and
parses them from your answer. To pick the box visually, use the GUI's box
picker (http://localhost:3000).

If you prefer local execution, core APIs remain available at localhost ports 8001-8004 (and Ollama at 11434 when its profile is running).

---

## Architecture Overview

### Container Structure

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Host System                        │
│                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  │
│  │ RCSB          │  │ Ligand        │  │ Protein       │  │
│  │ Downloader    │  │ Preparer      │  │ Preparer      │  │
│  │ :8001         │  │ :8002         │  │ :8003         │  │
│  └───────────────┘  └───────────────┘  └───────────────┘  │
│                                                               │
│  ┌───────────────┐  ┌───────────────┐                      │
│  │ Moldina       │  │ Web GUI       │                      │
│  │ Runner        │  │ (NiceGUI)     │                      │
│  │ :8004         │  │ :3000         │                      │
│  └───────────────┘  └───────────────┘                      │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Docker Bridge Network (auto-assigned subnet)        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   cache/    │  │   demo_     │  │   .env      │        │
│  │  (Volume)   │  │   output/   │  │  (Config)   │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### Image Hierarchy

```
mcp-docking-base:latest
    ├─ Python 3.12
    ├─ RDKit, Meeko, OpenBabel
    ├─ NumPy, SciPy
    └─ Common dependencies
        │
        ├── mcp-rcsb-downloader:latest
        │   └─ requests, RCSB API client
        │
        ├── mcp-ligand-preparer:latest
        │   └─ RDKit, Meeko
        │
        ├── mcp-protein-preparer:latest
        │   └─ RDKit
        │
        ├── mcp-moldina-runner:latest
        │   └─ Moldina/Vina binary
        │
        └── mcp-docking-ui:latest
            └─ NiceGUI web GUI + 3Dmol.js viewer
```

---

## Building Images

### Build All Images

```powershell
.\docker_build.ps1
```

### Build Specific Service

```powershell
.\docker_build.ps1 -Services rcsb-downloader,ligand-preparer
```

### Build Without Cache (Clean Build)

```powershell
.\docker_build.ps1 -NoCache
```

### Build in Parallel (Faster)

```powershell
.\docker_build.ps1 -Parallel
```

### Manual Build Commands

```bash
# Build base image first
docker build -f docker/base.Dockerfile -t mcp-docking-base:latest .

# Build individual services
docker build -f docker/rcsb_downloader.Dockerfile -t mcp-rcsb-downloader:latest .
docker build -f docker/ligand_preparer.Dockerfile -t mcp-ligand-preparer:latest .
docker build -f docker/protein_preparer.Dockerfile -t mcp-protein-preparer:latest .
docker build -f docker/moldina_runner.Dockerfile -t mcp-moldina-runner:latest .
docker build -f Dockerfile.ui -t mcp-docking-ui:latest .
```

---

## Running Services

### Start All Services

```bash
docker compose up -d --remove-orphans
```

`--remove-orphans` also stops containers of services that were removed from
`docker-compose.yml`. **Upgrading past Phase 16 (2026-10-26):** the
`ui-nice` service (NiceGUI on port 3001) is gone, since NiceGUI now serves
`ui` on port 3000. A plain `docker compose up -d` leaves the old
`mcp-ui-nice` container running as an orphan; use the command above, or
`docker rm -f mcp-ui-nice`. The installer and its launcher scripts pass
`--remove-orphans` themselves.

### Start Specific Services

```bash
docker compose up -d rcsb-downloader ligand-preparer
```

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f moldina-runner

# Last 100 lines
docker compose logs --tail=100
```

### Stop Services

```bash
docker compose down
```

### Restart Services

```bash
docker compose restart
```

### Stop and Remove Everything

```bash
docker compose down -v  # Removes volumes too
```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
# Logging
LOG_LEVEL=INFO

# Moldina Configuration
MOLDINA_PATH=/usr/local/bin/moldina

# Service URLs for container-based script execution
MCP_RCSB_DOWNLOADER_URL=http://rcsb-downloader:8001
MCP_LIGAND_PREPARER_URL=http://ligand-preparer:8002
MCP_PROTEIN_PREPARER_URL=http://protein-preparer:8003
MCP_MOLDINA_RUNNER_URL=http://moldina-runner:8004

# LLM configuration (GUI and nlp_orchestrator.py). Default: none -- the GUI's
# forms work without one. For the opt-in local Ollama:
# LLM_PROVIDER=ollama
# LLM_ENDPOINT=http://ollama:11434
# LLM_MODEL=gemma3:4b
LLM_PROVIDER=none
```

Moldina's CPU and memory limits are not environment variables: they are set
under `services.moldina-runner.deploy.resources.limits` in
`docker-compose.yml` (10 CPUs / 8G by default).

### Custom Moldina Binary

`docker/moldina_runner.Dockerfile` always builds from the binary committed
at `Moldina/moldina` (capital `M`, matching the repo's `Moldina/` directory
— there is no source download step in this Dockerfile to toggle). To use a
different build:

1. Overwrite `Moldina/moldina` with your binary.
2. Rebuild the image: `docker build -f docker/moldina_runner.Dockerfile -t mcp-moldina-runner:latest .`

See [Moldina/README.md](../Moldina/README.md) for the binary's provenance,
how to build it from source, and what to verify before replacing it.

---

## Volumes and Data Persistence

### Volume Mounts

Every MCP service bind-mounts three host directories:

1. **Cache** (`./cache` → `/app/cache`): downloaded PDB/ligand structures,
   shared across services so nothing is downloaded twice.
2. **Workflow output** (`./orchestrator_output` → `/app/orchestrator_output`):
   one `job_<id>/` directory per workflow run (GUI and CLI). The `ui` and
   `script-runner` containers mount the same directory at
   `/workspace/orchestrator_output`.
3. **Demo output** (`./demo_output` → `/app/demo_output`): output of the
   standalone demo scripts (e.g. `example_complete_workflow.py`).

The only named volume is `ollama-data` (the opt-in Ollama's models).

### Managing Volumes

```bash
# The bind mounts are plain host directories: back them up like any folder
tar czf cache-backup.tar.gz cache/
tar czf results-backup.tar.gz orchestrator_output/

# Named volumes (only ollama-data)
docker volume ls
```

---

## Testing

### Run Full Test Suite In Docker

```bash
# Make sure services are running
docker compose up -d

# Run all tests from script-runner (integration tests included)
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/ --tb=short

# Optional: run only slow tests
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/ -m "slow" --tb=short
```

`SERVICE_HOST=host.docker.internal` allows containerized tests to reach host-published service ports.

### GUI Tests

`nicegui`, `pytest-asyncio`, `pandas` and `plotly` are only in the `ui`
service's image (`Dockerfile.ui`), not in `script-runner`'s or
`requirements.txt`. So the tests that need them (`tests/test_nicegui_*.py`,
`tests/test_ui_comparison.py`, `tests/test_ui_results_rendering.py`)
`pytest.importorskip` them and skip under the default suite. Run them for
real via the `ui` image, with their own pytest config:

```bash
docker compose run --rm --no-deps ui \
  python -m pytest -c ui_nice/pytest.ini \
  tests/test_nicegui_*.py tests/test_ui_comparison.py tests/test_ui_results_rendering.py
```

This is a third test tier (alongside the hermetic default and the opt-in
`integration` marker). CI runs it in its own `gui-tier` job
(`.github/workflows/tests.yml`), which builds the base and `ui` images and
runs the same files in them. The
framework-free GUI logic in `ui/core/` is tested by `tests/test_ui_core_*.py`,
and `OrchestratorBridge` by `tests/test_ui_orchestrator_bridge.py`; both run
in the default hermetic suite (and CI).

- `ui_nice/pytest.ini` loads NiceGUI's `user_plugin` (the `user` /
  `create_user` fixtures, which simulate a browser without one) and
  `tests/nicegui_harness.py`. The harness isolates every test:
  `OUTPUT_BASE` in `tmp_path`, Ollama stubbed, `ui.settings.SETTINGS_FILE`
  patched, `OrchestratorBridge` replaced by the finished fake from
  `tests/ui_fakes.py`. It fixes the `LLM_*` environment variables too (no
  API key or base URL), since `docker compose run` passes the `.env` ones,
  real key included. It also shortens the progress timer to 50 ms, and its
  `page` fixture seeds each client's `GuiSession` before the page is drawn
  and keeps every page built. `tests/nicegui_harness.py`'s `element()`
  finds hidden elements too; `user.find` sees only visible ones (not the
  contents of a closed expansion).
- The 3Dmol viewer can't render without a browser, but it needs no
  recorder. The `MolViewer` element keeps what it last sent (`center`,
  `box_size`, `receptor_pdb`, ...), and tests read that through the
  harness's `viewer(user)`. That helper waits until the receptor has been
  read, which happens off the event loop. `atom_click(user, center)` fires
  the event that a click on a receptor atom sends, and
  `load_picker_receptor(page, center, size)` seeds a loaded receptor.
  What the viewer *draws* (`ui_nice/mol_viewer.js`) is outside this tier;
  check it in a real browser.
- The Results tab reads a result off the event loop, so tests
  `await results_loaded(page)` first. `completed_job_on_disk()` writes a
  completed session with a receptor and multi-pose SDFs. The `/files`
  download route is tested with httpx's `ASGITransport`, but the streaming
  test calls the ASGI app directly, because that transport buffers the
  body.
- The Sessions tab reads its lists off the event loop as well:
  `open_sessions(user, page)` opens the tab as a click does, and tests
  `await sessions_listed(page)` before looking at the rows.
- Many tests keep the names of the Streamlit render tests they replaced
  (Phase 16); the mapping is in `docs/reviews/stage_16_8_parity.md`.
- The test IDs print as `ui_nice::test_...`: with `-c`, pytest takes the
  config's directory as its rootdir. Cosmetic only.

### Manual Health Checks

```powershell
# PowerShell script to check all services
$ports = 8001..8004
foreach ($port in $ports) {
    $response = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing
    Write-Host "Port $port : $($response.StatusCode)" -ForegroundColor Green
}
```

```bash
# Bash script to check all services
for port in {8001..8004}; do
    curl -f http://localhost:$port/health && echo "Port $port: OK" || echo "Port $port: FAIL"
done
```

---

## Troubleshooting

### Services Won't Start

**Check logs:**
```bash
docker compose logs
```

**Common issues:**
- Port already in use: Change port mappings in docker-compose.yml
- Insufficient resources: Increase Docker Desktop resources
- Image build failed: Check `docker_build.ps1` output

### Service Unhealthy

**Check service status:**
```bash
docker compose ps
```

**Inspect container:**
```bash
docker inspect mcp-moldina-runner
```

**Check health check logs:**
```bash
docker inspect --format='{{json .State.Health}}' mcp-moldina-runner | python -m json.tool
```

### Moldina Not Found

**Problem:** "Moldina not found in container"

**Solutions:**
1. Check that the binary is in the image (Vina mode runs the same binary
   without the PSO flags, so there is no separate `vina`):
   ```bash
   docker exec mcp-moldina-runner which moldina
   ```

2. Provide custom Moldina binary (see Configuration section)

### Volume Permission Issues

**Problem:** Cannot write to mounted volumes

**Solution (Linux):**
```bash
# Set correct permissions
chmod -R 777 ./cache ./demo_output
```

**Solution (Windows):**
- Ensure Docker Desktop has access to the drive

**`orchestrator_output` specifically — `[Errno 13] Permission denied` on a
job's `downloads`/`prepared`/`docking_results`/`visualization` file:** this
is not a one-time host setup issue like the two above — it's a real
cross-container UID mismatch that the code now handles automatically, so
you shouldn't need to `chmod` anything by hand. Whichever process starts a
job (`script-runner`, or `nlp_orchestrator.py` run directly) creates each
job's output subdirectories as **root**, but `rcsb_downloader`,
`ligand_preparer`, `protein_preparer`, and `moldina_runner` write their
actual output files as the non-root `appuser` (uid 1000) — a directory
created by root with the default mode (755) blocks a different, non-root
uid from creating files in it. `services/llm_orchestrator/orchestrator.py`
creates every job-output subdirectory via
`common.utils.ensure_shared_directory()`, which explicitly widens
permissions after creation so this works out of the box. If you still hit
this (e.g. after manually creating a job directory by hand, or restoring
`orchestrator_output` from a backup/archive that preserved restrictive
permissions), the fix is the same as above — widen the specific job
directory:
```bash
docker compose --profile dev run --rm script-runner chmod -R 777 /workspace/orchestrator_output/job_<id>
```
- **Linux hosts, the GUI (Phase 16): `Permission denied` on a session,
  upload or settings file the former Streamlit GUI created.** Until Stage
  16.8 the GUI was Streamlit, running as root; the `ui` container now runs
  as the non-root `appuser` (uid 1000) on the same `orchestrator_output/`
  and `cache/`. Since Stage 16.3 the directories the GUI creates there (job
  dirs, `checkpoints/`, `saved_results/`, `uploads/`) are writable by every
  uid (`ensure_shared_directory()`), so is each job's `job.log`, and the
  JSON files are replaced rather than rewritten, which needs only the
  directory's permission. Each upload goes into a new directory of its own
  (`uploads/upload_<random>/<name>`), so no upload is ever written over.
  Files the root GUI created **before** 16.3, and deleting a job dir the
  root GUI created, still need root's files handed to uid 1000, once:
  ```bash
  sudo chown -R 1000:1000 orchestrator_output cache
  ```
  On Docker Desktop (Windows/macOS) files created now on a bind mount
  show up as owned by the container's user. But files that already
  carry root ownership are enforced: a root-owned 0644 upload from
  2026-10-02 refused an in-place write with `Errno 13`. If that happens
  there, run the same `chown` inside a root container, e.g.
  `docker exec -u root mcp-ui chown -R 1000:1000 /workspace/orchestrator_output /workspace/cache`.
- Settings → Resources → File Sharing

### Network Issues

**Problem:** Services cannot communicate

**Check network:**
```bash
docker network inspect docking_platform_mcp-network
```

**Test connectivity between containers:**
```bash
docker exec mcp-rcsb-downloader curl -s http://ligand-preparer:8002/health
```

### Integration Tests Cannot Reach Services

**Problem:** Docker integration tests fail to connect when run from script-runner.

**Fix:** Set service host for containerized test process.

```bash
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/test_docker_integration.py --tb=short
```

---

## Advanced Usage

### Custom Networks

Create isolated network for specific services:

```yaml
networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
    internal: true
```

### Resource Limits

Adjust limits in docker-compose.yml (current values shown):

```yaml
services:
  moldina-runner:
    deploy:
      resources:
        limits:
          cpus: '10'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 2G
```

`limits` is the enforced ceiling (a hard cgroup CPU quota / memory cap); `reservations`
is only a soft scheduling hint that isn't strongly enforced outside Swarm mode. Raising
`limits.cpus` also raises the ceiling the GUI's "Max parallel workers" control and the
`moldina_runner` service itself clamp to for [parallel docking](BATCH_DOCKING.md#optional-parallel-docking)
— but doesn't guarantee that many real cores if deployed on smaller hardware than the
quota assumes (both sides account for this, see the linked section for why). After
changing `limits.cpus` or `limits.memory`, re-run the
[benchmark](BATCH_DOCKING.md#benchmark-baseline-2026-10-05): the GUI's default worker
counts (`MEASURED_BEST_WORKERS` in `ui/worker_cap.py`, one per docking algorithm) were measured under the current
limits and don't follow them automatically; re-run with `--docking-algorithm vina` too.

### Multi-Stage Builds

Optimize image size with multi-stage builds:

```dockerfile
# Builder stage
FROM python:3.12-slim as builder
RUN pip install --user rdkit meeko

# Runtime stage
FROM python:3.12-slim
COPY --from=builder /root/.local /root/.local
```

### Docker Compose Profiles

`docker-compose.yml` defines two opt-in profiles. Every other service starts
with `docker compose up -d`.

- **`local-llm`** — the Ollama container (`mcp-llm`, port 11434). Off by
  default so a default install doesn't download the ~4.8 GB image and a
  3–5 GB model; most users either use a cloud provider or no LLM at all
  (Phase 18). Models persist in the `ollama-data` volume.
- **`dev`** — `script-runner`, for scripts and the test suite.

```bash
# Start the local LLM and download a model (or download it in the GUI's Settings)
docker compose --profile local-llm up -d ollama
docker exec -it mcp-llm ollama pull gemma3:4b

# Stop it again (the downloaded models stay in the volume)
docker compose --profile local-llm stop ollama

# Run a script or the test suite in the dev container
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health
```

Once Ollama runs, pick **Ollama (local)** in the GUI's Settings. Its
endpoint is `http://ollama:11434` (the default). An Ollama app installed on
the host itself is reachable at `http://host.docker.internal:11434`
(`ui` maps that name on Linux too).

### CI/CD Integration

GitHub Actions example:

```yaml
name: Build and Test Docker Images

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Build images
        run: docker compose build
      
      - name: Start services
        run: docker compose up -d
      
      - name: Wait for health
        run: sleep 30
      
      - name: Run tests
        run: pytest tests/test_docker_integration.py
      
      - name: Stop services
        run: docker compose down
```

---

## Production Deployment

### Security Model: Single-Machine Deployment Only

**This platform ships with no authentication on any service.** Every MCP service
(`rcsb-downloader`, `ligand-preparer`, `protein-preparer`, `moldina-runner`),
the GUI (`ui`), and Ollama accept
requests from anyone who can reach their port — there is no API key, login, or
per-request auth check anywhere in `common/mcp_base.py` or the service servers.

To keep this safe, `docker-compose.yml` binds every published port to
`127.0.0.1` (loopback) instead of all network interfaces, e.g.:

```yaml
ports:
  - "127.0.0.1:8001:8001"  # not reachable from other machines
```

This means the platform is only intended to be run **on a single trusted
machine**, accessed by the person running Docker Compose on that machine. It is
**not safe to deploy as-is** on a shared server, behind a reverse proxy exposed
to a LAN/VPN, or anywhere another user or process could reach these ports —
doing so would expose unauthenticated job execution and file read/write.

**A remembered API key is stored in plain text.** When "Remember API key on
this computer" is ticked in the GUI's Settings, the key is written to
`./cache/.docking_platform_settings.json` (mode 0600: readable only by the
GUI's user, uid 1000 in the container). Anyone with that user's access, or
root, on the machine can read it; leave the box unticked to keep the key in
the browser session only, or set `LLM_API_KEY` in `.env` instead. On Docker
Desktop for Windows the mode holds inside Docker (checked in Review Stage
R5: a container running as another uid gets "Permission denied"), but on the
host itself the file is as private as the repo folder's own permissions, like
`.env` next to it.

If you need multi-user or remote/server deployment, do **not** simply remove
the `127.0.0.1` binding. That requires additional hardening that is not yet
implemented — a shared API-key/auth layer in front of every service, hardening
of user-supplied paths, and TLS via a reverse proxy. This work is tracked as a
future phase in `dev_plan.md`.

### Additional Hardening (once auth exists)

1. **Use secrets for sensitive data**:
   ```yaml
   secrets:
     api_key:
       file: ./secrets/api_key.txt
   ```

2. **Enable TLS/SSL** with reverse proxy (nginx/traefik)

(Every image already runs as the non-root `appuser`.)

### Scaling Services

`docker compose --scale` does not work as the file stands: each service has a
fixed `container_name` and a fixed host port. Running several docking
runners would need those removed and a load balancer (e.g. nginx) in front.
Within one runner, use parallel ligand docking instead (see
[BATCH_DOCKING.md](BATCH_DOCKING.md)).

### Monitoring

Use Prometheus + Grafana:

```yaml
services:
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
  
  grafana:
    image: grafana/grafana
    ports:
      - "3002:3000"  # host 3002 -- 3000 is this platform's GUI (`ui`)
```

---

## Command Cheat Sheet

Quick-reference commands beyond the walkthroughs above. Everything here uses
the same `/mcp` JSON-RPC endpoint documented in [API.md](API.md) — POST with
`{"jsonrpc": "2.0", "id": ..., "method": "tool_call", "params": {"name": ..., "arguments": {...}}}`.

### GPU Check (Ollama)

Only when the `local-llm` profile is running. The Ollama container runs on
the CPU: its GPU block in `docker-compose.yml` is commented out. Enable it
there if `nvidia-container-toolkit` is installed, then check:

```bash
# Verify GPU device request was applied to the ollama container
docker inspect mcp-llm --format "Runtime={{.HostConfig.Runtime}} DeviceRequests={{json .HostConfig.DeviceRequests}}"

# Check active model processor split (CPU/GPU)
docker exec mcp-llm ollama ps
```

### Per-Service Commands

**RCSB Downloader (port 8001):**
```bash
docker compose logs -f rcsb-downloader
curl -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tool_call","params":{"name":"download_structures","arguments":{"protein_id":"1ABC","save_dir":"./data/test"}}}'
```

**Ligand Preparer (port 8002):**
```bash
docker compose logs -f ligand-preparer
docker exec mcp-ligand-preparer python -c "import rdkit; print(rdkit.__version__)"
```

**Protein Preparer (port 8003):**
```bash
docker compose logs -f protein-preparer
docker exec mcp-protein-preparer python -c "import rdkit, gemmi; print('OK')"
```

**Moldina Runner (port 8004):**
```bash
docker compose logs -f moldina-runner
docker exec mcp-moldina-runner which moldina
docker exec mcp-moldina-runner moldina --version
docker stats mcp-moldina-runner
```

### Useful Aliases

Add to your shell profile (`.bashrc`, `.zshrc`, or PowerShell profile):

```bash
# Bash/Zsh
alias dps='docker compose ps'
alias dlogs='docker compose logs -f'
alias dup='docker compose up -d'
alias ddown='docker compose down'
alias drestart='docker compose restart'
```

```powershell
# PowerShell
function dps { docker compose ps @args }
function dlogs { docker compose logs -f @args }
function dup { docker compose up -d @args }
function ddown { docker compose down @args }
function drestart { docker compose restart @args }
```

---

## Summary

You now have a fully containerized MCP Docking System that:

- Requires only Docker (no local Moldina or RDKit installation).
- Works across Windows, Linux, and macOS.
- Includes health checks and resource limits.
- Includes comprehensive tests.

**Next steps:**
1. Build images: `./docker_build.ps1`
2. Start services: `docker compose up -d`
3. Open the GUI at http://localhost:3000 and click **Run Docking** (or, with an
   LLM configured, run a natural-language workflow from the CLI as in step 5
   above)
4. View results in the GUI's Results tab, or in `./orchestrator_output/<job_id>/`

For issues or questions, check the [Troubleshooting](#troubleshooting) section or open an issue on GitHub.
