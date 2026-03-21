# MCP-Based Docking System

A fully containerized molecular docking pipeline using Model Context Protocol (MCP) to coordinate protein preparation, ligand preparation, Moldina PSO-based docking, and PyMOL visualization.

**Zero Installation Required:** Everything runs in Docker containers, so you do not need to install Moldina, PyMOL, RDKit, or other dependencies locally.

**Natural Language Interface:** Control the workflow with text commands powered by a local Ollama LLM.

## Architecture

```
                 +---------------------------+
                 |  Orchestrator (Agent)     |  <-- Natural language command
                 |  - Parser / slot-filler   |
                 |  - Planner                |
                 |  - MCP client (caller)    |
                 +---------------------------+
                          | JSON-RPC (MCP tool_call)
   -------------------------------------------------------------
   |                |                |                         |
+------+  +-----------+     +-----------+         +-----------+
|RCSB  |  | LigPrep   |     | Moldina   |         | PyMOL     |
|Down- |  | (prep)    |     | (docking) |         | (visual)  |
|loader|  |           |     |           |         |           |
+------+  +-----------+     +-----------+         +-----------+
```

## Components

1. **RCSB Downloader** - Fetches protein structures and ligands from RCSB PDB.
2. **Protein Preparer** - Cleans proteins, adds hydrogens, and converts to PDBQT.
3. **Ligand Preparer** - Prepares ligands (protonation, 3D conformers, PDBQT conversion).
4. **Moldina Runner** - Executes Moldina PSO-based molecular docking.
5. **PyMOL Visualizer** - Generates visualization scripts and rendered outputs.
6. **LLM Orchestrator** - Provides natural-language workflow orchestration via Ollama.

All services run in Docker containers.

## Quick Start

### Natural Language Interface (Easiest!)

**Control the entire workflow with simple text commands:**

```powershell
# 1. Start all services (including Ollama LLM)
docker compose up -d

# 2. Download the LLM model (once, ~4 GB for Llama 2)
docker exec -it mcp-ollama ollama pull llama2

# 3. Check everything is healthy from script-runner (no local Python setup required)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# 4. Run a complete workflow with natural language
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download protein 6lu7 and ligand ATP, prepare them, and dock with center at 10,20,30 and box size 25,30,35"

# Optional local execution (if you want host Python)
# python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"

# Verbosity options (default is 'low' for clean output):
# --verbose low        # Minimal output (default)
# --verbose normal     # Show service logs
# --verbose mcp_demo   # Full MCP JSON-RPC demonstration

# Results automatically saved to ./nlp_orchestration_output/<job_id>/
```

**See [docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md) for complete guide and examples.**

### Docker Deployment (Recommended - 5 Minutes!)

**Prerequisites:** Docker Desktop (Windows/Mac) or Docker Engine (Linux)

```bash
# 1. Build all Docker images (~5-10 minutes first time)
.\docker_build.ps1      # Windows PowerShell
# ./docker_build.sh     # Linux/Mac

# 2. Start all services (~30 seconds)
docker compose up -d

# 3. Check services are healthy
docker compose ps

# 4. Run complete workflow from Docker dev script-runner
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"

# 5. Optional: run full tests in Docker
docker compose --profile dev run --rm -e SERVICE_HOST=host.docker.internal script-runner python -m pytest tests/ --tb=short

# 6. View results
pymol .\demo_output\visualization\visualization.pse
```

**See [DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md) for detailed guide.**

### Local Installation (Optional)

**Note:** Docker is recommended. Local installation only needed for development.

**See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed instructions.**

```bash
# Python 3.9+ required (not Python 2.7!)
conda create -n docking-system python=3.12
conda activate docking-system
conda install -c conda-forge rdkit openbabel meeko
pip install -r requirements.txt

# Start services individually (ports 8001-8005)
python3 -m services.rcsb_downloader.server
python3 -m services.ligand_preparer.server
python3 -m services.protein_preparer.server
python3 -m services.moldina_runner.server
python3 -m services.pymol_visualizer.server
```

### Docker Deployment Details

**No installation required!** All dependencies (Moldina, PyMOL, RDKit) are included in containers.

**Services:**
- RCSB Downloader (port 8001) - ~2 GB
- Ligand Preparer (port 8002) - ~2 GB
- Protein Preparer (port 8003) - ~2 GB
- Moldina Runner (port 8004) - ~2 GB with custom Moldina executable
- PyMOL Visualizer (port 8005) - ~3.5 GB with conda PyMOL + headless rendering

**See [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md) for complete Docker guide.**

## Example Workflow

### Using Natural Language

```powershell
# Simple command-line mode
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
# Or use python3 if needed:
# python3 nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"

# Interactive mode with prompts
python nlp_orchestrator.py --interactive

# Example outputs:
# 🤖 Parsing workflow...
# 📋 Workflow Plan:
#   1. Download protein: 6lu7
#   2. Download ligand: ATP  
#   3. Prepare ligands
#   4. Prepare protein
#   5. Run docking
#   6. Create visualization
# 
# Missing required parameter: center (docking box center coordinates)
# Please provide: x,y,z coordinates in Angstroms
# Your input: 10,20,30
#
# ✅ Executing workflow...
# ✅ Results saved to ./nlp_orchestration_output/job_20240115_123456/
```

### Using Python API

```bash
# Using Python 3 (not Python 2.7!)
$env:PYTHONIOENCODING="utf-8"  # Windows: Enable UTF-8 output
python example_complete_workflow.py
# Or use python3 if needed:
# python3 example_complete_workflow.py

# Output:
# ✅ Downloaded protein: 6LU7.pdb
# ✅ Prepared protein: 6LU7.pdbqt  
# ✅ Prepared ligands: ATP.pdbqt, HEM.pdbqt
# ✅ Docking completed: Best affinity -2.4 kcal/mol
# ✅ Visualization created: PNG + PyMOL session
```

**Results in `./demo_output/`:**
- `downloaded/` - Raw PDB/SDF files
- `prepared_proteins/` - PDBQT receptor files
- `prepared_ligands/` - PDBQT ligand files
- `docking_results/` - Docked poses from Moldina
- `visualization/` - PyMOL scripts, session files, rendered images

## Development

### Project Structure

```
mcp_demo/
├── common/                      # Shared utilities and base classes
│   ├── __init__.py
│   ├── mcp_base.py             # Base MCP server implementation
│   ├── schemas.py              # JobSpec and other schemas
│   └── utils.py                # Common utilities
│
├── services/                    # MCP service implementations
│   ├── rcsb_downloader/        # Protein/ligand download service
│   │   ├── __init__.py
│   │   └── server.py
│   ├── ligand_preparer/        # Ligand preparation service
│   │   ├── __init__.py
│   │   └── server.py
│   ├── protein_preparer/       # Protein preparation service
│   │   ├── __init__.py
│   │   └── server.py
│   ├── moldina_runner/         # Molecular docking service
│   │   ├── __init__.py
│   │   └── server.py
│   ├── pymol_visualizer/       # Visualization service
│   │   ├── __init__.py
│   │   └── server.py
│   └── llm_orchestrator/       # LLM-based workflow orchestration
│       ├── __init__.py
│       ├── llm_client.py       # Ollama LLM client
│       ├── orchestrator.py     # Workflow executor
│       └── workflow_parser.py  # Workflow validator
│
├── tests/                       # Unit and integration tests
│   ├── fixtures/               # Test data (benzene.sdf, water.sdf)
│   ├── test_*.py               # Individual service tests
│   ├── test_integration.py     # End-to-end integration tests
│   └── test_docker_integration.py  # Docker container tests
│
├── docker/                      # Docker configurations
│   ├── base.Dockerfile         # Base image with common dependencies
│   ├── rcsb_downloader.Dockerfile
│   ├── ligand_preparer.Dockerfile
│   ├── protein_preparer.Dockerfile
│   ├── moldina_runner.Dockerfile
│   └── pymol_visualizer.Dockerfile
│
├── docs/                        # Documentation
│   ├── API.md                  # MCP tool specifications
│   ├── INSTALLATION.md         # Installation guide
│   ├── DOCKER_DEPLOYMENT.md    # Docker deployment guide
│   ├── LLM_ORCHESTRATION.md    # Natural language interface guide
│   ├── DOCKING_PARAMETERS.md   # Docking parameters reference
│   ├── VERBOSITY_LEVELS.md     # Verbosity modes guide
│   └── *.md                    # Additional documentation
│
├── dev_reports/                 # Development reports (gitignored)
│   └── *.md                    # Analysis reports, implementation notes
│
├── Moldina/                     # Moldina executable
│   └── moldina                 # PSO-based docking binary
│
├── cache/                       # Downloaded structures cache
├── demo_output/                 # Example workflow outputs
├── orchestrator_output/         # LLM orchestrator outputs
├── test_output/                 # Test outputs
│
├── nlp_orchestrator.py          # CLI for natural language workflows
├── example_complete_workflow.py # Example Python API usage
├── example_prepare_ligand.py    # Example ligand preparation
│
├── docker-compose.yml           # Multi-service Docker orchestration
├── docker_build.ps1/.sh         # Docker build scripts
├── start_services.ps1           # Service startup script (local)
├── restart_servers.ps1          # Service restart script
│
├── requirements.txt             # Python dependencies
├── pytest.ini                   # Pytest configuration
├── .gitignore                   # Git ignore rules
├── README.md                    # This file
└── QUICK_START.md              # Quick start guide
```

### Running Tests

```bash
# Run all unit tests
python3 -m pytest tests/ -v

# Run Docker integration tests (requires services running)
docker compose up -d
docker compose --profile dev run --rm -e SERVICE_HOST=host.docker.internal script-runner python -m pytest tests/test_docker_integration.py -v

# Expected: ~110+ tests passing
```

## Key Features

- **Natural Language Control** - Command workflows with plain text.
- **Three Verbosity Levels** - Choose minimal, detailed, or protocol-level output.
- **Local LLM** - Ollama-based parsing and planning, with GPU support when available.
- **Containerized Deployment** - Run all dependencies in Docker.
- **Moldina PSO Docking** - PSO-based docking for pose search.
- **Headless PyMOL** - Automated rendering in containers.
- **Cross-Platform** - Works across Windows and Linux path styles.
- **Caching** - Reuses downloaded structures to reduce repeated work.
- **MCP Protocol** - Standard JSON-RPC interface across services.

## Important Notes

### Python Version
- **Use `python3` command** (Python 3.12+) when `python` points to an incompatible interpreter
- System `python` may be Python 2.7 (not compatible)
- Check: `python3 --version` should show 3.9+

### Windows Users
- Use PowerShell (not CMD)
- Set UTF-8 encoding: `$env:PYTHONIOENCODING="utf-8"`
- Use `python3` not `python`

### PyMOL Analysis
- Generated `.pse` session files work with local PyMOL
- Rendered `.png` images created in Docker automatically
- Paths are relative, work on host and in containers

## Documentation

- **[docs/VERBOSITY_LEVELS.md](docs/VERBOSITY_LEVELS.md)** - Guide to verbosity levels (`low`, `normal`, `mcp_demo`)
- **[docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md)** - Natural language interface guide and examples
- **[docs/DOCKING_PARAMETERS.md](docs/DOCKING_PARAMETERS.md)** - Comprehensive guide to all docking parameters across all stages
- **[docs/API.md](docs/API.md)** - Complete MCP tool specifications and JSON-RPC examples
- **[docs/INSTALLATION.md](docs/INSTALLATION.md)** - Installation instructions (Docker and local)
- **[docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)** - Docker deployment guide

## License

MIT License
