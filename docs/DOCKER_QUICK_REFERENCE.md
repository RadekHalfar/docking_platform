# Docker Quick Reference - MCP Docking System

## Build Commands

```bash
# Build all images
docker compose build

# Build specific service
docker compose build rcsb-downloader

# Build with no cache (clean build)
docker compose build --no-cache

# Build using helper scripts
./docker_build.ps1              # Windows
./docker_build.sh               # Linux/Mac
```

## Run Commands

```bash
# Start all services in background
docker compose up -d

# Start specific services
docker compose up -d rcsb-downloader ligand-preparer

# Start with log output
docker compose up

# Stop all services
docker compose down

# Stop and remove volumes
docker compose down -v

# Restart services
docker compose restart

# Restart specific service
docker compose restart moldina-runner
```

## Monitoring Commands

```bash
# View running containers
docker compose ps

# View all containers (including stopped)
docker ps -a

# View service logs
docker compose logs -f

# View logs for specific service
docker compose logs -f pymol-visualizer

# View last 100 lines
docker compose logs --tail=100 moldina-runner

# Follow logs from specific time
docker compose logs --since 10m -f
```

## Script Runner (Dev Profile)

```bash
# Run orchestrator health check inside Docker (no local Python setup required)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# Run workflow command
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"

# Run workflow with explicit model (no rebuild needed)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP, prepare and dock"

# Explicit provider + endpoint + model
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-provider ollama --llm-endpoint http://ollama:11434 --llm-model llama2 "Download 6lu7"
```

## Test Commands

```bash
# Run full test suite from script-runner (includes docker integration tests)
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/ --tb=short

# Run only slow tests
docker compose --profile dev run --rm \
  -e SERVICE_HOST=host.docker.internal \
  script-runner python -m pytest tests/ -m "slow" --tb=short
```

## Health Checks

```bash
# Check all service health endpoints
for port in {8001..8005}; do curl -f http://localhost:$port/health; done

# PowerShell version
8001..8005 | ForEach-Object { Invoke-WebRequest "http://localhost:$_/health" }

# Check specific service
curl http://localhost:8001/health
```

## Debugging Commands

```bash
# Execute command in running container
docker exec -it mcp-moldina-runner bash

# View container filesystem
docker exec mcp-moldina-runner ls -la /app

# Check environment variables
docker exec mcp-moldina-runner env

# View container processes
docker exec mcp-moldina-runner ps aux

# Inspect container configuration
docker inspect mcp-moldina-runner

# View container resource usage
docker stats

# View specific container stats
docker stats mcp-moldina-runner
```

## Volume Management

```bash
# List volumes
docker volume ls

# Inspect volume
docker volume inspect docking_platform_cache-data

# Remove unused volumes
docker volume prune

# Backup volume
docker run --rm \
  -v docking_platform_cache-data:/cache \
  -v $(pwd):/backup \
  ubuntu tar czf /backup/cache-backup.tar.gz /cache

# Restore volume
docker run --rm \
  -v docking_platform_cache-data:/cache \
  -v $(pwd):/backup \
  ubuntu tar xzf /backup/cache-backup.tar.gz -C /
```

## Network Management

```bash
# List networks
docker network ls

# Inspect network
docker network inspect docking_platform_mcp-network

# Test connectivity between containers
docker exec mcp-rcsb-downloader ping mcp-ligand-preparer

# View network connections
docker network inspect docking_platform_mcp-network -f '{{json .Containers}}'
```

## Image Management

```bash
# List images
docker images

# List MCP images only
docker images | grep mcp

# Remove image
docker rmi mcp-rcsb-downloader:latest

# Remove all MCP images
docker images | grep mcp | awk '{print $3}' | xargs docker rmi

# Remove unused images
docker image prune

# Remove all unused images
docker image prune -a

# View image layers
docker history mcp-docking-base:latest

# View image size
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
```

## Cleanup Commands

```bash
# Remove stopped containers
docker container prune

# Remove unused volumes
docker volume prune

# Remove unused networks
docker network prune

# Remove unused images
docker image prune

# Remove everything unused
docker system prune

# Remove everything including volumes
docker system prune -a --volumes

# Stop and remove all MCP containers
docker compose down -v
docker ps -a | grep mcp | awk '{print $1}' | xargs docker rm -f
```

## GPU Check (Ollama)

```bash
# Verify GPU device request was applied to ollama container
docker inspect mcp-llm --format "Runtime={{.HostConfig.Runtime}} DeviceRequests={{json .HostConfig.DeviceRequests}}"

# Check active model processor split (CPU/GPU)
docker exec mcp-llm ollama ps
```

## Service-Specific Commands

### RCSB Downloader (Port 8001)

```bash
# View logs
docker compose logs -f rcsb-downloader

# Test download
curl -X POST http://localhost:8001/jsonrpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"download_structure","params":{"structure_id":"1ABC","structure_type":"protein","format":"pdb"},"id":1}'
```

### Ligand Preparer (Port 8002)

```bash
# View logs
docker compose logs -f ligand-preparer

# Check RDKit installation
docker exec mcp-ligand-preparer python -c "import rdkit; print(rdkit.__version__)"
```

### Protein Preparer (Port 8003)

```bash
# View logs
docker compose logs -f protein-preparer

# Check available tools
docker exec mcp-protein-preparer which python
docker exec mcp-protein-preparer python -c "import rdkit, gemmi; print('OK')"
```

### Moldina Runner (Port 8004)

```bash
# View logs
docker compose logs -f moldina-runner

# Check Moldina installation
docker exec mcp-moldina-runner which moldina
docker exec mcp-moldina-runner moldina --version

# Monitor CPU/memory usage during docking
docker stats mcp-moldina-runner
```

### PyMOL Visualizer (Port 8005)

```bash
# View logs
docker compose logs -f pymol-visualizer

# Check PyMOL installation
docker exec mcp-pymol-visualizer pymol -c -Q -e "quit"

# Check Xvfb (virtual display)
docker exec mcp-pymol-visualizer ps aux | grep Xvfb

# Test rendering
docker exec mcp-pymol-visualizer pymol -c -Q test.pml
```

## Troubleshooting

### Port Already in Use

```bash
# Find what's using the port
lsof -i :8001          # Linux/Mac
netstat -ano | findstr :8001    # Windows

# Change port in docker-compose.yml
ports:
  - "8011:8001"  # Map to different host port
```

### Container Won't Start

```bash
# View detailed logs
docker logs mcp-moldina-runner

# Check exit code
docker inspect mcp-moldina-runner --format='{{.State.ExitCode}}'

# Try running interactively
docker run -it --rm mcp-moldina-runner:latest bash
```

### Service Unhealthy

```bash
# Check health status
docker inspect mcp-moldina-runner --format='{{json .State.Health}}' | python -m json.tool

# View health check logs
docker inspect mcp-moldina-runner --format='{{range .State.Health.Log}}{{.Output}}{{end}}'

# Manually test health endpoint
docker exec mcp-moldina-runner curl -f http://localhost:8004/health
```

### Volume Permission Issues

```bash
# Linux: Set permissions
sudo chown -R $(id -u):$(id -g) ./cache ./demo_output
chmod -R 755 ./cache ./demo_output

# Check volume permissions in container
docker exec mcp-rcsb-downloader ls -la /app/cache
```

## Development Workflow

```bash
# 1. Make code changes
# 2. Rebuild affected service
docker compose build ligand-preparer

# 3. Restart service
docker compose restart ligand-preparer

# 4. View logs
docker compose logs -f ligand-preparer

# 5. Test changes
curl http://localhost:8002/health
```

## Performance Optimization

```bash
# Use BuildKit for faster builds
DOCKER_BUILDKIT=1 docker compose build

# Limit Moldina runner resources
docker compose up -d --scale moldina-runner=1

# View resource usage
docker stats --no-stream

# Clear build cache
docker builder prune
```

## Production Deployment

```bash
# Build for production
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# Start in production mode
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Enable auto-restart
docker update --restart=unless-stopped $(docker ps -q)

# View container uptime
docker ps --format "table {{.Names}}\t{{.Status}}"
```

## Useful Aliases

Add to your shell profile (`.bashrc`, `.zshrc`, or PowerShell profile):

```bash
# Bash/Zsh
alias dps='docker compose ps'
alias dlogs='docker compose logs -f'
alias dup='docker compose up -d'
alias ddown='docker compose down'
alias drestart='docker compose restart'
alias dexec='docker compose exec'
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

**For detailed information, see [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)**
