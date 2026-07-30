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

Optional interactive service:
```bash
docker compose --profile interactive up -d pymol-interactive
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

If you have a custom Moldina binary:

1. Place it in `./moldina/moldina`
2. Uncomment the COPY line in `docker/moldina_runner.Dockerfile`:
   ```dockerfile
   COPY moldina/moldina /usr/local/bin/moldina
   RUN chmod +x /usr/local/bin/moldina
   ```
3. Comment out the Vina download section
4. Rebuild the image

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

Run different service combinations:

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

### Security Considerations

1. **Don't expose all ports publicly**:
   ```yaml
   ports:
     - "127.0.0.1:8001:8001"  # Bind to localhost only
   ```

2. **Use secrets for sensitive data**:
   ```yaml
   secrets:
     api_key:
       file: ./secrets/api_key.txt
   ```

3. **Run as non-root user**:
   ```dockerfile
   RUN useradd -m appuser
   USER appuser
   ```

4. **Enable TLS/SSL** with reverse proxy (nginx/traefik)

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
      - "3000:3000"
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
