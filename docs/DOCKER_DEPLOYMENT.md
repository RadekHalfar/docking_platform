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
mcp-pymol-visualizer    mcp-pymol-visualizer:latest  Up (healthy)
mcp-llm                 ollama/ollama:latest         Up (healthy)
```

`pymol-interactive` is not gated by a profile -- it already started with the rest in step 2. To restart just that one service (e.g. after a targeted rebuild):
```bash
docker compose up -d pymol-interactive
docker compose ps pymol-interactive
```

### 4. Test a Service

```bash
curl http://localhost:8001/health
```

Expected response:
```json
{"status": "healthy", "service": "rcsb_downloader", "version": "1.0"}
```

### 5. Run Workflow Without Local Python Environment

Use the dev profile script runner. The workspace is bind-mounted into the container,
so script edits on host are picked up immediately (no image rebuild needed).

```powershell
# Check orchestration health from inside container network
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# Run full natural-language workflow
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
```

If docking coordinates are missing, the CLI prompts for box source:
- Manual entry (`center`/`box_size`)
- PyMOL Interactive definition

When PyMOL mode is selected, the orchestrator launches an interactive selection flow and creates `orchestrator_output/<job_id>/docking_box.json` from the current selection.

If you prefer local execution, core APIs remain available at localhost ports 8001-8005 and Ollama at 11434.
The optional interactive PyMOL service exposes noVNC on 6080 and helper API on 8090.

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
│  │ Moldina       │  │ PyMOL         │                      │
│  │ Runner        │  │ Visualizer    │                      │
│  │ :8004         │  │ :8005         │                      │
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
        ├── mcp-pymol-visualizer:latest
        │   └─ PyMOL, Xvfb (virtual display)
        │
        └── mcp-pymol-interactive:latest
          └─ PyMOL GUI + noVNC + helper API
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
docker build -f docker/pymol_visualizer.Dockerfile -t mcp-pymol-visualizer:latest .
docker build -f docker/pymol_interactive.Dockerfile -t mcp-pymol-interactive:latest .
```

---

## Running Services

### Start All Services

```bash
docker compose up -d
```

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

# PyMOL Configuration  
DISPLAY=:99

# Service URLs for container-based script execution
MCP_RCSB_DOWNLOADER_URL=http://rcsb-downloader:8001
MCP_LIGAND_PREPARER_URL=http://ligand-preparer:8002
MCP_PROTEIN_PREPARER_URL=http://protein-preparer:8003
MCP_MOLDINA_RUNNER_URL=http://moldina-runner:8004
MCP_PYMOL_VISUALIZER_URL=http://pymol-visualizer:8005
MCP_PYMOL_INTERACTIVE_URL=http://pymol-interactive:8090

# LLM configuration for nlp_orchestrator.py
LLM_PROVIDER=ollama
LLM_ENDPOINT=http://ollama:11434
LLM_MODEL=qwen2.5-coder:7b-instruct-q4_K_M

# Optional backward-compatible alias (legacy)
OLLAMA_URL=http://ollama:11434

# Resource Limits
MOLDINA_CPU_LIMIT=4
MOLDINA_MEMORY_LIMIT=4G
```

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

The docker-compose.yml configures two main volumes:

1. **Cache Volume** (`./cache` → `/app/cache`):
   - Stores downloaded PDB structures
   - Shared across all services
   - Improves performance by avoiding re-downloads

2. **Output Volume** (`./demo_output` → `/app/output`):
   - Stores processing results
   - Accessible from host system
   - Contains docking results and visualizations

### Managing Volumes

```bash
# View volumes
docker volume ls

# Inspect a volume
docker volume inspect docking_platform_cache-data

# Backup cache
docker run --rm -v docking_platform_cache-data:/cache -v $(pwd):/backup ubuntu tar czf /backup/cache-backup.tar.gz /cache

# Restore cache
docker run --rm -v docking_platform_cache-data:/cache -v $(pwd):/backup ubuntu tar xzf /backup/cache-backup.tar.gz -C /

# Clean up unused volumes
docker volume prune
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

### Manual Health Checks

```powershell
# PowerShell script to check all services
$ports = 8001..8005
foreach ($port in $ports) {
    $response = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing
    Write-Host "Port $port : $($response.StatusCode)" -ForegroundColor Green
}
```

```bash
# Bash script to check all services
for port in {8001..8005}; do
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

### PyMOL Rendering Issues

**Problem:** "Cannot connect to display"

**Solution:** The pymol-visualizer uses Xvfb (virtual display):
```bash
# Check if Xvfb is running in container
docker exec mcp-pymol-visualizer ps aux | grep Xvfb
```

### Moldina Not Found

**Problem:** "Moldina not found in container"

**Solutions:**
1. Check if Vina is installed:
   ```bash
   docker exec mcp-moldina-runner which moldina
   docker exec mcp-moldina-runner which vina
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
- Settings → Resources → File Sharing

### Network Issues

**Problem:** Services cannot communicate

**Check network:**
```bash
docker network inspect docking_platform_mcp-network
```

**Test connectivity between containers:**
```bash
docker exec mcp-rcsb-downloader ping mcp-ligand-preparer
```

### Integration Tests Cannot Reach Services

**Problem:** Docker integration tests fail to connect when run from script-runner.

**Fix:** Set service host for containerized test process.

```bash
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/test_docker_integration.py --tb=short
```

### Unknown Mark Warning For slow

If pytest still prints `PytestUnknownMarkWarning: slow` in your environment,
the test still runs correctly and passes; this is an environment-specific warning with pytest 9.

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
quota assumes (both sides account for this, see the linked section for why).

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

Illustrative pattern for splitting services into opt-in groups -- **not** what
`docker-compose.yml` currently defines (today the only profile is `dev`, on
`script-runner`; every other service, including `pymol-interactive`, starts
unconditionally with `docker compose up -d`). Adopt something like this if you
need to split the stack for a leaner deployment:

```yaml
services:
  moldina-runner:
    profiles: ["full", "docking"]
  
  pymol-visualizer:
    profiles: ["full", "visualization"]

  pymol-interactive:
    profiles: ["interactive", "full", "visualization"]
```

```bash
# Start only docking services
docker compose --profile docking up -d

# Start everything
docker compose --profile full up -d

# Start browser-based interactive PyMOL only
docker compose --profile interactive up -d pymol-interactive
```

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
(`rcsb-downloader`, `ligand-preparer`, `protein-preparer`, `moldina-runner`,
`pymol-visualizer`, `pymol-interactive`), the Streamlit UI, and Ollama accept
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
doing so would expose unauthenticated job execution, file read/write, and (for
`pymol-interactive`) an unauthenticated remote desktop and known code-injection
paths.

If you need multi-user or remote/server deployment, do **not** simply remove
the `127.0.0.1` binding. That requires additional hardening that is not yet
implemented — a shared API-key/auth layer in front of every service, a VNC
password for `pymol-interactive` (it currently runs `x11vnc -nopw`), hardening
of user-supplied paths, and TLS via a reverse proxy. This work is tracked as a
future phase in `dev_plan.md`.

### Additional Hardening (once auth exists)

1. **Use secrets for sensitive data**:
   ```yaml
   secrets:
     api_key:
       file: ./secrets/api_key.txt
   ```

2. **Run as non-root user**:
   ```dockerfile
   RUN useradd -m appuser
   USER appuser
   ```

3. **Enable TLS/SSL** with reverse proxy (nginx/traefik)

### Scaling Services

```bash
# Scale moldina runner to 3 instances
docker compose up -d --scale moldina-runner=3

# Use load balancer (e.g., nginx) to distribute requests
```

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
      - "3001:3000"  # host 3001 -- 3000 is already used by this platform's own GUI (the `ui` service)
```

---

## Command Cheat Sheet

Quick-reference commands beyond the walkthroughs above. Everything here uses
the same `/mcp` JSON-RPC endpoint documented in [API.md](API.md) — POST with
`{"jsonrpc": "2.0", "id": ..., "method": "tool_call", "params": {"name": ..., "arguments": {...}}}`.

### GPU Check (Ollama)

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

- Requires only Docker (no local Moldina, PyMOL, or RDKit installation).
- Works across Windows, Linux, and macOS.
- Includes health checks and resource limits.
- Supports scaling and deployment workflows.
- Includes comprehensive tests.

**Next steps:**
1. Build images: `./docker_build.ps1`
2. Start services: `docker compose up -d`
3. Run workflow: `docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"`
4. View results in `./demo_output/`

For issues or questions, check the [Troubleshooting](#troubleshooting) section or open an issue on GitHub.
