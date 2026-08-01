# Installation Guide

## ⚡ Recommended: Docker Deployment

**No local installation needed!** Docker containers include all dependencies (Moldina, PyMOL, RDKit).

**Prerequisites:**
- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- 8 GB RAM minimum, 16 GB recommended
- 20 GB free disk space

**Quick Start:**
```bash
# See ../DOCKER_QUICK_REFERENCE.md for complete guide
.\docker_build.ps1        # Build images (Windows)
docker compose up -d      # Start services
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
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
`pymol-visualizer`, `pymol-interactive`, `ui`) are only ever built locally
from the Dockerfiles in this repo — they are not published to any container
registry — so the installer needs the Dockerfiles alongside `docker-compose.yml`,
which means a git clone. (Only the Ollama LLM image is pulled from a public
registry; that happens automatically as part of `docker compose up`.)

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
- *Ollama not running / chat parsing fails*: the bundled `ollama` service
  should start automatically with the stack; check `docker compose logs
  ollama` if it doesn't.
- *Installer times out at step [5/5]*: run `docker compose logs` to see
  which service failed to become healthy.

This installer is a build-it-yourself tool today — there isn't yet a
published one-click download. Until then, prefer the Docker deployment
steps above.

---

## Alternative: Local Installation (Development Only)

Only use local installation for development or when Docker is unavailable.

### Prerequisites

- **Python 3.9+ (NOT Python 2.7!)**
- **Conda** (Anaconda or Miniconda) - Required for chemistry packages
- **Git** (optional, for cloning repository)

## Installation Methods

### Method 1: Conda Environment (Recommended)

Use conda for reliable chemistry package installation (RDKit, OpenBabel, Meeko).

```bash
# 1. Create conda environment
conda create -n docking-system python=3.12
conda activate docking-system

# 2. Navigate to project directory
cd docking-system

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install chemistry packages via conda
conda install -c conda-forge rdkit openbabel meeko

# 5. Download spaCy language model (if needed)
python3 -m spacy download en_core_web_sm
```

### Method 2: pip Installation (No Conda)

Use this when you only need basic services or already manage Python dependencies globally.

```bash
# 1. Verify Python version
python3 --version  # Should show 3.9 or higher

# 2. Navigate to project directory
cd docking-system

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install chemistry packages (if available on your platform)
pip install rdkit meeko

# 5. Download spaCy model
python3 -m spacy download en_core_web_sm
```

### Method 3: Minimal Installation (Core Services Only)

Install only core dependencies without chemistry packages.

```bash
# 1. Navigate to project directory
cd docking-system

# 2. Install core dependencies
pip install requests flask python-dotenv spacy pytest pytest-cov pytest-mock pydantic jsonschema pyyaml

# 3. Download spaCy model
python -m spacy download en_core_web_sm
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
# Should be 3.9 or higher
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

### Skip Tests Requiring Optional Dependencies
```bash
# Skip tests requiring RDKit if not installed
pytest tests/ -v -m "not rdkit"
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

### Check Service Health
```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

## Testing Services

### Using Test Client
```bash
# Make sure service is running first
python test_client.py
```

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
cd docking-system
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
PYMOL_VISUALIZER_PORT=8015
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

1. **Read the documentation**: `docs/API.md`, `docs/LIGAND_PREPARATION.md`
2. **Run the tests**: `pytest tests/ -v`
3. **Start services**: Try the RCSB Downloader first
4. **Test with client**: Run `python test_client.py`
5. **Explore examples**: Check the test files for usage examples

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
