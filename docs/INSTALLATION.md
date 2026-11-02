# Installation Guide

## ⚡ Recommended: Docker Deployment

**No local installation needed!** Docker containers include all dependencies (Moldina, RDKit, OpenBabel, Meeko).

**Prerequisites:**
- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- 8 GB RAM (16 GB recommended if you add the optional local LLM)
- 10 GB free disk space: the images take about 2.2 GB, and building them
  needs room for intermediate layers. The optional local LLM adds about
  8–9.5 GB (the Ollama image plus a model).

**Quick Start:**
```bash
# See DOCKER_DEPLOYMENT.md for the complete guide
.\docker_build.ps1        # Build images (Windows)
docker compose up -d      # Start services
# Then dock from the GUI at http://localhost:3000 (no LLM needed).
# The natural-language CLI needs an LLM first (see LLM_ORCHESTRATION.md), e.g.:
docker compose --profile local-llm up -d ollama
docker exec -it mcp-llm ollama pull gemma3:4b
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-provider ollama --llm-model gemma3:4b "Download 6lu7 and ATP, prepare and dock"
```

**→ For Docker deployment, see [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)**

---

## Installer Script (Convenience Wrapper — Requires a Clone)

`installer/install.py` automates the steps above **for people who already
have this repo cloned**: it checks for Docker (installing it if missing),
builds the platform's images (`docker compose build`), starts the stack,
waits for the GUI to become healthy, opens your browser to it, and writes a
launcher script (`docking-platform.bat` / `.command` / `.sh`) into the repo
so you can double-click that next time instead of re-running the installer.

**It is not a zero-dependency, no-clone download.** The platform's own images
(`rcsb-downloader`, `ligand-preparer`, `protein-preparer`, `moldina-runner`,
`ui`) are only ever built locally
from the Dockerfiles in this repo — they are not published to any container
registry — so the installer needs the Dockerfiles alongside `docker-compose.yml`,
which means a git clone. No LLM is installed: the GUI's forms work without
one, and an LLM can be added later (a cloud provider's API key, or the
opt-in local Ollama; see the README's "Choose an LLM").

Run it from the repo root — it's a single, dependency-free Python script, no
`pip install` needed to run it from source:

```bash
# See the steps it would take without actually doing anything:
python installer/install.py --dry-run

# Run for real (installs Docker if needed, builds images, starts the stack,
# opens the browser). Must be run from inside the clone:
python installer/install.py

# Or point it at a compose file explicitly, e.g. from another directory:
python installer/install.py --compose-file /path/to/docking_platform/docker-compose.yml
```

**System requirements:** 8 GB RAM, 10 GB free disk space, a Docker-compatible
OS (Windows 10/11, macOS, or Linux).

**Building a standalone binary:** if you'd rather double-click an `.exe` than
run a Python script, freeze it with PyInstaller via `installer/build.sh` (or
the `Build Installer` GitHub Actions workflow, which builds it for Windows,
Mac, and Linux). This produces `dist/docking-platform-installer[.exe]`. Copy
it into the repo root (or always pass `--compose-file`) before running it —
double-clicking it from an arbitrary folder with no `docker-compose.yml`
nearby will fail with a clear "not found, run this from inside a clone"
message rather than doing anything unexpected.

**Troubleshooting:**
- *"docker-compose.yml not found"*: you're not running the installer from
  inside a clone of this repo. `cd` into it, or pass `--compose-file`.
- *Docker Desktop won't start / installer hangs at step [2/5]*: start Docker
  Desktop manually and re-run the installer — it will skip straight to
  building images once the daemon is reachable.
- *`docker compose build` fails at step [3/5]*: make sure you have the full
  repo checked out (the `Dockerfile*` files must be present, not just
  `docker-compose.yml`).
- *`docker compose up` fails at step [4/5]* with a container-name conflict:
  another instance of the stack is already running (e.g. started manually
  elsewhere) — stop it first with `docker compose down`.
- *Port 3000 already in use*: stop whatever else is bound to it, or edit
  `docker-compose.yml`'s `ui.ports` mapping and re-run
  `docker compose up -d`.
- *The chat says it needs an LLM*: none is configured by default. Pick a
  provider in the GUI's Settings. For a local one, start Ollama first:
  `docker compose --profile local-llm up -d ollama`.
- *Chat parsing fails with Ollama selected*: the Ollama container isn't
  running (it's opt-in) or has no model yet. Settings says which: "Ollama
  isn't running" (start it as above, then **Re-check**), or "No models
  downloaded yet" (download one there, e.g. `gemma3:4b`).
- *Installer times out at step [5/5]*: run `docker compose logs` to see
  which service failed to become healthy.

This installer is a build-it-yourself tool today — there isn't yet a
published one-click download. Until then, prefer the Docker deployment
steps above.

---

## Alternative: Local Installation (Development Only)

Only use local installation for development or when Docker is unavailable.

### Prerequisites

- **Python 3.10+ (NOT Python 2.7!)**; the Docker images and CI use 3.12
- **Conda** (Anaconda or Miniconda) - Required for chemistry packages
- **Git** (optional, for cloning repository)

## Installation Methods

### Method 1: Conda Environment (Recommended)

Use conda for reliable chemistry package installation (RDKit, OpenBabel, Meeko).

```bash
# 1. Create conda environment
conda create -n docking-platform python=3.12
conda activate docking-platform

# 2. Navigate to project directory (the cloned repo root)
cd docking_platform

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install chemistry packages via conda
conda install -c conda-forge rdkit openbabel meeko
```

### Method 2: pip Installation (No Conda)

Use this when you only need basic services or already manage Python dependencies globally.

```bash
# 1. Verify Python version
python3 --version  # Should show 3.10 or higher

# 2. Navigate to project directory (the cloned repo root)
cd docking_platform

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install chemistry packages (if available on your platform)
pip install rdkit meeko
```

### Method 3: Minimal Installation (Core Services Only)

Install only core dependencies without chemistry packages.

```bash
# 1. Navigate to project directory (the cloned repo root)
cd docking_platform

# 2. Install core dependencies
pip install requests flask waitress python-dotenv pytest pytest-cov pytest-mock pydantic jsonschema pyyaml
```

**Note:** Ligand Preparer service will not work without RDKit.

### Method 4: Docker Installation (Recommended)

All services and dependencies are pre-packaged in Docker images.

```bash
docker compose up -d
```

## Verifying Installation

### Check Python Version
```bash
python --version
# Should be 3.10 or higher
```

### Check RDKit Installation
```python
python -c "from rdkit import Chem; print('RDKit OK')"
```

### Check Meeko Installation (Optional but Recommended)
```python
python -c "from meeko import MoleculePreparation; print('Meeko OK')"
```

### Check OpenBabel Installation (Optional)
```bash
obabel --version
```

## Running Tests

### All Tests
```bash
pytest tests/ -v
```

### Specific Test Files
```bash
# Core tests (no chemistry packages needed)
pytest tests/test_schemas.py tests/test_utils.py tests/test_mcp_base.py -v

# RCSB Downloader tests (no chemistry packages needed)
pytest tests/test_rcsb_downloader.py -v

# Ligand Preparer tests (requires RDKit)
pytest tests/test_ligand_preparer.py -v
```

### With Coverage Report
```bash
pytest tests/ --cov=. --cov-report=html
# Open htmlcov/index.html in browser
```

### Tests Requiring Optional Dependencies
Tests that need RDKit (or Meeko) skip themselves when it isn't installed, so
no marker is needed. To skip the tests that need running services:
```bash
pytest tests/ -v -m "not integration"
```

## Starting Services

Each MCP service can be run independently:

### RCSB Downloader
```bash
python -m services.rcsb_downloader.server
# Runs on http://localhost:8001
```

### Ligand Preparer (Requires RDKit)
```bash
python -m services.ligand_preparer.server
# Runs on http://localhost:8002
```

### Protein Preparer
```bash
python -m services.protein_preparer.server
# Runs on http://localhost:8003
```

### Moldina Runner (Requires the Moldina binary)
```bash
python -m services.moldina_runner.server
# Runs on http://localhost:8004
```

### Check Service Health
```bash
curl http://localhost:8001/health
# {"service": "rcsb_downloader", "status": "ok"}
```

## Testing Services

### Using curl
```bash
# List available tools
curl -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/list"
  }'

# Download a structure
curl -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tool_call",
    "params": {
      "name": "download_structures",
      "arguments": {
        "protein_id": "1CRN",
        "save_dir": "./data/test"
      }
    }
  }'
```

## Troubleshooting

### Issue: "RDKit not found"
**Solution:** Install via pip (or conda):
```bash
# Using pip:
pip install rdkit

# Or using conda:
conda install -c conda-forge rdkit
```

### Issue: "Meeko not found"
**Solution:** Install via pip (or conda):
```bash
# Using pip:
pip install meeko

# Or using conda:
conda install -c conda-forge meeko
```

### Issue: "ModuleNotFoundError: No module named 'common'"
**Solution:** Make sure you're running from the project root directory and Python can find the modules:
```bash
cd docking_platform
export PYTHONPATH="${PYTHONPATH}:$(pwd)"  # Linux/Mac
set PYTHONPATH=%PYTHONPATH%;%CD%  # Windows
```

### Issue: "Address already in use"
**Solution:** Service port is occupied. Either:
- Stop the existing service
- Change the **host** port in `.env` (only remaps what's exposed to your machine; the container keeps using its normal internal port, so no other config needs to change):
```bash
RCSB_DOWNLOADER_PORT=8011
LIGAND_PREPARER_PORT=8012
PROTEIN_PREPARER_PORT=8013
MOLDINA_RUNNER_PORT=8014
```
Then restart with `docker compose up -d` (or `docker compose down && docker compose up -d` if services are already running) for the new mapping to take effect.

### Issue: "pytest: command not found"
**Solution:** Install pytest:
```bash
pip install pytest pytest-cov pytest-mock
```

### Issue: Tests fail with "RDKit not installed"
**Solution:** This is expected if RDKit is not installed. Tests that require RDKit are automatically skipped. To run all tests, install RDKit via conda.

## Environment Configuration

Create a `.env` file in the project root:

```bash
# Copy example file
cp .env.example .env

# Edit as needed
nano .env  # or your preferred editor
```

Key settings:
```bash
# Service ports
RCSB_DOWNLOADER_PORT=8001
LIGAND_PREPARER_PORT=8002

# Data directories
DATA_DIR=./data
CACHE_DIR=./cache

# Logging
LOG_LEVEL=INFO
```

## Development Setup

### Install Development Dependencies
```bash
pip install black flake8 mypy
```

### Code Formatting
```bash
black services/ common/ tests/
```

### Linting
```bash
flake8 services/ common/ tests/ --max-line-length=120
```

### Type Checking
```bash
mypy services/ common/
```

## Next Steps

After installation:

1. **Read the documentation**: [../QUICK_START.md](../QUICK_START.md) for the
   fastest path to a working workflow, then `docs/API.md` for the wire-level
   reference and `docs/LLM_ORCHESTRATION.md` for the natural-language CLI
2. **Run the tests**: `pytest tests/ -v`
3. **Start services**: Try the RCSB Downloader first
4. **Explore examples**: Check `example_complete_workflow.py` and the test
   files for direct-API usage examples

## Minimum System Requirements

- **CPU**: 2 cores
- **RAM**: 4 GB (8 GB recommended with RDKit)
- **Disk**: 2 GB for installation, additional space for data
- **OS**: Windows 10/11, Linux, macOS

## Recommended System Requirements

- **CPU**: 4+ cores
- **RAM**: 16 GB
- **Disk**: 10 GB+ (for docking data)
- **OS**: Linux (Ubuntu 20.04+) or macOS

## Support

For issues and questions:
1. Check this installation guide
2. Review documentation in `docs/`
3. Check test files for examples
4. Review error messages and logs
