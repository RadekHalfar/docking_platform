# MCP-Based Docking System

A fully containerized molecular docking pipeline using Model Context Protocol (MCP) to coordinate protein preparation, ligand preparation, Moldina PSO-based docking, and PyMOL visualization.

**Zero Installation Required:** Everything runs in Docker containers, so you do not need to install Moldina, PyMOL, RDKit, or other dependencies locally.

**Natural Language Interface:** Control the workflow with text commands powered by a modular LLM layer (default provider: local Ollama).

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
6. **LLM Orchestrator** - Provides natural-language workflow orchestration via pluggable LLM providers (default: Ollama).

All services run in Docker containers.

## Quick Start

### Natural Language Interface (Easiest!)

**Control the entire workflow with simple text commands:**

```powershell
# 1. Start all services (including Ollama LLM)
docker compose up -d

# 2. Download the default LLM model (Qwen 2.5 Coder 7B Q4, ~4.7 GB)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# Optional: switch model at runtime (no rebuild needed)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP, prepare and dock"

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

### LLM Model Selection (No Rebuild Needed!)

Switch between different LLM models at runtime without rebuilding Docker images:

#### Available Models

| Model | Download Size | Speed | Best For |
|-------|---|---|---|
| **Qwen 2.5 Coder 7B Q4** (default) | 4.7 GB | Medium | Code-heavy workflows, strong instruction following |
| **DeepSeek Coder 6.7B Q4** | 4.1 GB | Medium | Complex reasoning, code understanding |
| **Mistral 7B Q4** | 4.0 GB | Medium | Balanced performance, strong reasoning |
| **Phi-3 Mini Q4** | 2.0 GB | Fast | Quick responses, lower memory |
| **Llama 2 7B Q4** | 3.8 GB | Medium | General purpose |

#### How to Use Different Models

**Step 1: Download a model into the LLM container** (one-time per model)
```powershell
# Qwen 2.5 Coder 7B Q4 (project default, already pre-installed)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# DeepSeek
docker exec -it mcp-llm ollama pull deepseek-coder:6.7b-instruct-q4_K_M

# Mistral
docker exec -it mcp-llm ollama pull mistral

# Phi-3 (lightweight)
docker exec -it mcp-llm ollama pull phi3

# Llama 2
docker exec -it mcp-llm ollama pull llama2
```

**Step 2: Use the model in your workflows** (just add `--llm-model` flag)
```powershell
# Use default model
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"

# Use Mistral instead
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP, prepare and dock"

# Use Qwen 2.5 Coder instead
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP, prepare and dock"

# Use lightweight Phi-3 for faster inference
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-model phi3 "Download 6lu7 and ATP, prepare and dock"

# List available models
docker exec -it mcp-llm ollama list
```

#### Key Features

✅ **No Docker rebuild** – Just pull a model and use `--llm-model` flag  
✅ **Multiple providers** – Extensible architecture supports other LLM APIs  
✅ **Environment variables** – Set `LLM_MODEL`, `LLM_ENDPOINT`, `LLM_PROVIDER` for custom configuration  
✅ **Model persistence** – Models are cached in the `ollama-data` volume (survives container restarts)

**See [docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md) for advanced configuration and troubleshooting.**

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

# 6. Optional: start interactive PyMOL in browser (no host PyMOL needed)
docker compose --profile interactive up -d pymol-interactive
# open: http://localhost:6080/vnc.html
# helper API: http://localhost:8090
# If your workflow command includes "visualize", the CLI will auto-load that job into pymol-interactive when this service is running.
# Missing docking center/box_size uses hybrid clarification by default:
#   - LLM interprets manual vs PyMOL preference from natural text
#   - Manual path parses center/box_size from free-text answer
#   - Ambiguous intent falls back to deterministic 1/2 menu
# Use deterministic-only behavior with:
#   --clarification-mode legacy
# PyMOL path creates docking_box.json from your current PyMOL selection.

# 7. View rendered image output from containerized PyMOL
# .\demo_output\visualization\docking_visualization.png
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

# Start core API services individually (ports 8001-8005)
python3 -m services.rcsb_downloader.server
python3 -m services.ligand_preparer.server
python3 -m services.protein_preparer.server
python3 -m services.moldina_runner.server
python3 -m services.pymol_visualizer.server

# Optional interactive browser service (ports 6080/8090)
# docker compose --profile interactive up -d pymol-interactive
```

### Docker Deployment Details

**No installation required!** All dependencies (Moldina, PyMOL, RDKit) are included in containers.

**Services:**
- RCSB Downloader (port 8001) - ~2 GB
- Ligand Preparer (port 8002) - ~2 GB
- Protein Preparer (port 8003) - ~2 GB
- Moldina Runner (port 8004) - ~2 GB with custom Moldina executable
- PyMOL Visualizer (port 8005) - ~3.5 GB with conda PyMOL + headless rendering
- PyMOL Interactive (ports 6080/8090) - Browser-based noVNC session + docking-box helper API

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

# Phase-1 interactive commands:
#   state                 -> show active job state from disk
#   new <request>         -> start a fresh workflow in current session
#   end / quit / exit     -> stop session

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
# Required parameters needing clarification:
#   - The protein PDB ID is invalid. Please provide a 4-character ID (e.g., '6lu7').
#   - Please provide docking box center coordinates [x, y, z] (e.g., '10, 20, 30').
# The protein PDB ID looks invalid. Please enter a valid 4-character ID (e.g., 6lu7):
# I am missing docking box center [x, y, z]. Do you want to use default [10, 20, 30]? [Y/n]:
#
# ✅ Executing workflow...
# ✅ Results saved to ./orchestrator_output/job_20240115_123456/
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
│   ├── pymol_interactive/      # Interactive noVNC PyMOL helper service
│   │   ├── __init__.py
│   │   └── server.py
│   └── llm_orchestrator/       # LLM-based workflow orchestration
│       ├── __init__.py
│       ├── llm_base.py         # Provider interface
│       ├── llm_adapters.py     # Provider implementations (Ollama adapter)
│       ├── llm_factory.py      # Provider factory
│       ├── workflow_llm_parser.py  # Provider-neutral workflow parsing
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
│   ├── pymol_visualizer.Dockerfile
│   └── pymol_interactive.Dockerfile
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
- **Modular LLM Layer** - Provider interface + adapters (default provider is Ollama) with runtime model selection.
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
- **[docs/PYMOL_INTERACTIVE.md](docs/PYMOL_INTERACTIVE.md)** - Browser-based PyMOL interactive service and docking box helper API
- **[docs/INSTALLATION.md](docs/INSTALLATION.md)** - Installation instructions (Docker and local)
- **[docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)** - Docker deployment guide

## License

MIT License
