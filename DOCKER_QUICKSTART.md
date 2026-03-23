# Docker Quick Start Guide

Start the MCP Docking System with Docker.

## Prerequisites

- **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux)
- 8 GB RAM minimum, 16 GB recommended
- 20 GB free disk space

## Step 1: Build Images (5-10 minutes)

### Windows PowerShell
```powershell
cd docking-system
.\docker_build.ps1
```

### Linux/macOS
```bash
cd docking-system
chmod +x docker_build.sh
./docker_build.sh
```

### Alternative: Use docker-compose
```bash
docker compose build
```

**Expected output:**
```
✅ Successfully built base
✅ Successfully built rcsb-downloader
✅ Successfully built ligand-preparer
✅ Successfully built protein-preparer
✅ Successfully built moldina-runner
✅ Successfully built pymol-visualizer
🎉 All images built successfully!
```

## Step 2: Start Services (30 seconds)

```bash
docker compose up -d
```

**Expected output:**
```
Creating mcp-rcsb-downloader ... done
Creating mcp-ligand-preparer ... done
Creating mcp-protein-preparer ... done
Creating mcp-moldina-runner ... done
Creating mcp-pymol-visualizer ... done
```

## Step 3: Verify Services (5 seconds)

```bash
docker compose ps
```

**Expected output:**
```
NAME                   STATUS
mcp-rcsb-downloader    Up (healthy)
mcp-ligand-preparer    Up (healthy)
mcp-protein-preparer   Up (healthy)
mcp-moldina-runner     Up (healthy)
mcp-pymol-visualizer   Up (healthy)
mcp-ollama             Up (healthy)
```

**Health check URLs:**
- RCSB Downloader: http://localhost:8001/health
- Ligand Preparer: http://localhost:8002/health
- Protein Preparer: http://localhost:8003/health
- Moldina Runner: http://localhost:8004/health
- PyMOL Visualizer: http://localhost:8005/health

## Step 4: Run Workflow

The services are now running and accessible via HTTP on ports 8001-8005.

### Option A: Use the Example Workflow Script

```powershell
# Ensure output directory exists
mkdir -p demo_output

# Run complete workflow
python example_complete_workflow.py
```

The script will:
1. Download protein structure (6LU7) from RCSB
2. Download ligands (ATP, HEM) from RCSB
3. Prepare protein for docking
4. Prepare ligands for docking
5. Run Moldina docking
6. Generate PyMOL visualization

### Option B: Call Services Directly

#### Download a Protein

```powershell
$body = @{
    jsonrpc = "2.0"
    method = "download_structure"
    params = @{
        structure_id = "1ABC"
        structure_type = "protein"
        format = "pdb"
        save_dir = "./demo_output/downloaded"
    }
    id = 1
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8001/jsonrpc" -Method Post -Body $body -ContentType "application/json"
```

### Option C: Run Scripts in Docker (No Local Python Setup, No Rebuild for Script Edits)

Use the `script-runner` service (profile `dev`) to execute scripts from your host-mounted workspace.
When you edit a `.py` file on host, just rerun the command, no image rebuild is needed.

```powershell
# Health check through nlp_orchestrator.py
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# Run a natural-language workflow command
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --verbose normal "Download 6lu7 and ATP, prepare and dock"

# Switch LLM model at runtime (no rebuild required)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP, prepare and dock"

# You can also override provider/endpoint explicitly
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-provider ollama --llm-endpoint http://ollama:11434 --llm-model llama2 "Download 6lu7 and ATP"
```

Rebuild only when dependencies or Dockerfiles change.

### Option D: Run Full Test Suite In Docker

```powershell
docker compose --profile dev run --rm `
  -e SERVICE_HOST=host.docker.internal `
  script-runner python -m pytest tests/ --tb=short
```

This runs unit and Docker integration tests from inside the containerized dev environment.

#### Check Moldina Installation

```bash
docker exec mcp-moldina-runner moldina --version
```

## Step 5: View Results

```bash
# View logs
docker compose logs -f

# Check output
ls demo_output/

# View visualization in PyMOL (if installed locally)
pymol demo_output/visualization/visualization.pml
```

## Common Commands

### Start/Stop

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# Restart a service
docker compose restart ligand-preparer

# View status
docker compose ps
```

### Monitoring

```bash
# View logs (all services)
docker compose logs -f

# View logs (specific service)
docker compose logs -f moldina-runner

# View resource usage
docker stats

# Check health
curl http://localhost:8001/health
```

### Debugging

```bash
# Enter container shell
docker exec -it mcp-moldina-runner bash

# View files in container
docker exec mcp-moldina-runner ls -la /app

# Check processes
docker exec mcp-moldina-runner ps aux

# Inspect container
docker inspect mcp-moldina-runner
```

### Cleanup

```bash
# Stop and remove containers
docker compose down

# Remove volumes too
docker compose down -v

# Remove all MCP images
docker images | grep mcp | awk '{print $3}' | xargs docker rmi
```

## Troubleshooting

### Services Won't Start

**Check logs:**
```bash
docker compose logs
```

**Common fixes:**
- Ensure ports 8001-8005 are available
- Increase Docker Desktop memory (Settings → Resources)
- Restart Docker Desktop

### Port Conflicts

If ports are already in use, change them in `docker-compose.yml`:

```yaml
services:
  rcsb-downloader:
    ports:
      - "8011:8001"  # Changed from 8001:8001
```

### Service Unhealthy

Wait 30-60 seconds for services to initialize:

```bash
# Watch status
watch docker compose ps

# Check specific health
docker inspect mcp-moldina-runner --format='{{json .State.Health}}'
```

### Out of Disk Space

```bash
# Clean up unused Docker resources
docker system prune -a

# Remove old images
docker image prune -a
```

## What's Included?

✅ **All Dependencies Installed:**
- Python 3.12
- RDKit (chemistry library)
- Meeko (ligand preparation)
- OpenBabel (format conversion)
- AutoDock Vina (molecular docking)
- PyMOL (visualization)
- NumPy, SciPy, and more

✅ **No Host Installation Required:**
- Moldina/Vina included in container
- PyMOL with headless rendering
- All Python packages pre-installed

✅ **Production Ready:**
- Health checks
- Auto-restart
- Resource limits
- Volume persistence
- GPU acceleration for Ollama (if NVIDIA runtime is available)

## Next Steps

1. **Read Full Guide**: [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)
2. **Command Reference**: [docs/DOCKER_QUICK_REFERENCE.md](docs/DOCKER_QUICK_REFERENCE.md)
3. **Run Tests**: `docker compose --profile dev run --rm -e SERVICE_HOST=host.docker.internal script-runner python -m pytest tests/ --tb=short`
4. **Customize**: Edit `docker-compose.yml` for your needs

## Performance Tips

### Build Faster
```bash
# Use BuildKit
DOCKER_BUILDKIT=1 docker compose build

# Build in parallel
./docker_build.ps1 -Parallel
```

### Run Efficiently
```bash
# Limit Moldina resources
# Edit docker-compose.yml:
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
```

### Cache Structures
```bash
# Cache volume persists between runs
# Located at: ./cache/
# Significantly speeds up repeated workflows
```

## Getting Help

- **Logs**: `docker compose logs -f [service]`
- **Shell**: `docker exec -it [container] bash`
- **Health**: `curl http://localhost:800X/health`
- **Stats**: `docker stats`

**For detailed troubleshooting, see [docs/DOCKER_DEPLOYMENT.md#troubleshooting](docs/DOCKER_DEPLOYMENT.md#troubleshooting)**

---

**That's it! You're running a complete molecular docking system with zero dependency installation.**

Happy docking! 🧬🔬
