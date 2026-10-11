# MCP-Based Docking System

A fully containerized molecular docking pipeline using Model Context Protocol (MCP) to coordinate protein preparation, ligand preparation, Moldina PSO-based docking, and an in-browser 3Dmol.js visualization.

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
|RCSB  |  | LigPrep   |     | Moldina   |         | LigPrep   |
|Down- |  | (prep +   |     | (docking) |         | pose      |
|loader|  |  poses)   |     |           |         | -> GUI    |
+------+  +-----------+     +-----------+         +-----------+
```

3D viewing itself happens client-side in the browser (3Dmol.js) — the
fourth box above converts docked poses to PDB/SDF/MOL2, it doesn't render
anything server-side.

## Components

1. **RCSB Downloader** - Fetches protein structures and ligands from RCSB PDB.
2. **Protein Preparer** - Cleans proteins, adds hydrogens, and converts to PDBQT.
3. **Ligand Preparer** - Prepares ligands (protonation, 3D conformers, PDBQT conversion), and converts docked poses to PDB/SDF/MOL2 for the viewer/downloads.
4. **Moldina Runner** - Executes Moldina PSO-based molecular docking.
5. **GUI 3D Viewer** - In-browser 3Dmol.js viewer + visual docking-box picker (no server-side rendering; see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)). A legacy PyMOL Visualizer service still exists for direct/optional use — see [docs/PYMOL_VISUALIZATION.md](docs/PYMOL_VISUALIZATION.md).
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

# Results automatically saved to ./orchestrator_output/<job_id>/
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
# GUI (Streamlit) tests need the ui image instead (see docs/DOCKER_DEPLOYMENT.md#gui-streamlit-unit-tests):
docker compose run --rm ui python -m pytest tests/test_ui_*.py -v

# 6. Interactive PyMOL in browser is already running (started in step 2 along
#    with everything else -- no extra flag or command needed)
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

# 7. View results: open the GUI (http://localhost:3000, started in step 2) and
#    load the job for an in-browser 3Dmol.js view + PDBQT/PDB/SDF/MOL2 downloads,
#    or inspect the converted pose files directly:
# .\demo_output\visualization\*_poses.sdf
```

**See [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md) for the detailed guide.**

### Local Installation (Optional)

**Note:** Docker is recommended. Local installation only needed for development.

**See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed instructions.**

```bash
# Python 3.9+ required (not Python 2.7!)
conda create -n docking-platform python=3.12
conda activate docking-platform
conda install -c conda-forge rdkit openbabel meeko
pip install -r requirements.txt

# Start core API services individually (ports 8001-8005)
python3 -m services.rcsb_downloader.server
python3 -m services.ligand_preparer.server
python3 -m services.protein_preparer.server
python3 -m services.moldina_runner.server
python3 -m services.pymol_visualizer.server

# Optional interactive browser service (ports 6080/8090) -- runs in Docker
# even if you're running the other services locally; no profile flag needed
# docker compose up -d pymol-interactive
```

### Docker Deployment Details

**No installation required!** All dependencies (Moldina, PyMOL, RDKit) are included in containers.

**Services:**
- RCSB Downloader (port 8001) - ~2 GB
- Ligand Preparer (port 8002) - ~2 GB
- Protein Preparer (port 8003) - ~2 GB
- Moldina Runner (port 8004) - ~2 GB with custom Moldina executable
- PyMOL Visualizer (port 8005) - ~3.5 GB with conda PyMOL + headless rendering. Optional/legacy — the default `visualize` step no longer calls it (see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)).
- PyMOL Interactive (ports 6080/8090) - Browser-based noVNC session + docking-box helper API. Still used for the CLI's interactive box selection; the GUI has its own lighter, PyMOL-free visual box picker.

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

# Interactive commands:
#   state                          -> show active job, parameters, last status
#   history                        -> show revision history table
#   change ligand to HEM           -> update parameters (LLM-extracted)
#   set center to 10,20,30         -> update docking box center
#   rerun / dock                   -> re-execute invalidated steps only
#   save results as <name>         -> save named result bundle (docking + visualization)
#   save docking as <name>         -> save docking-only bundle
#   save visualization as <name>   -> save visualization-only bundle
#   undo <revision>                -> restore parameters/state to a prior revision
#   new <request>                  -> start a fresh workflow in current session
#   end / quit / exit              -> stop session
#
# Chained commands: join multiple follow-up commands with "and"/"then" to run
# them in order after one confirmation, e.g.:
#   🧬 > state and save results as trial_a
#   🧬 > change ligand to HEM and rerun docking
#
# Example multi-variant session:
#   🧬 > Download 6lu7 and ATP, prepare and dock with center 10,20,30 and box 25,25,25
#   🧬 > save results as atp_box1
#   🧬 > change ligand to HEM
#   🧬 > rerun
#   🧬 > save results as hem_box1
#   🧬 > set center to 15,18,7
#   🧬 > rerun
#   🧬 > save docking as hem_box2
#   🧬 > history
#   🧬 > undo 3
#   🧬 > end
#
# Note: save is blocked when parameters have been changed but not yet re-executed.
# Run 'rerun' first to sync results with the current parameters.
# The GUI has the same history / undo / save-as commands (panel controls and
# chat) -- see docs/GUI_GUIDE.md.

# Example outputs:
# 🤖 Parsing workflow...
# 📋 Workflow Plan:
#   1. Get protein: 6lu7
#   2. Get ligand: ATP  
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

`example_complete_workflow.py` calls each MCP service directly (bypassing the
orchestrator), including `pymol_visualizer`'s `visualize_docking` — it's a
standalone demo of the raw service APIs, not the default `visualize` step the
GUI/orchestrator use (see [Components](#components) above and
[docs/GUI_GUIDE.md](docs/GUI_GUIDE.md) for that path).

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
- `visualization/` - PyMOL scripts, session files, rendered images (this script's own output — the main GUI/orchestrator workflow writes PDB/SDF/MOL2 pose files here instead, see above)

## Development

### Project Structure

```
docking_platform/
├── common/                      # Shared utilities used across services/ui/cli
│   ├── mcp_base.py             # Base MCP server implementation (Flask + JSON-RPC)
│   ├── mcp_bootstrap.py        # Shared --host/--port/--cache-dir CLI parsing
│   ├── mcp_client.py           # Shared JSON-RPC MCP client (used by orchestrator + examples)
│   ├── mcp_tooling.py          # Tool-parameter registration helpers
│   ├── http_retry.py           # Retry/backoff policies for LLM vs MCP HTTP calls
│   ├── file_io.py              # Atomic JSON writes, cross-platform FileLock
│   ├── file_utils.py           # Local-folder ligand scanning
│   ├── path_utils.py           # Host<->container path conversion
│   ├── config.py               # Env-var-backed service port/cache-dir defaults
│   ├── schemas.py              # JobSpec and MCP tool schemas
│   └── utils.py                # Validation, checksums, misc helpers
│
├── services/                    # MCP service implementations
│   ├── rcsb_downloader/        # Protein/ligand download service (port 8001)
│   ├── ligand_preparer/        # Ligand prep + docked-pose->PDB/SDF/MOL2 conversion (port 8002)
│   ├── protein_preparer/       # Protein preparation service (port 8003)
│   ├── moldina_runner/         # Molecular docking service (port 8004)
│   ├── pymol_visualizer/       # Headless PyMOL rendering (port 8005) -- optional/legacy, not used by default
│   ├── pymol_interactive/      # Interactive noVNC PyMOL helper service (noVNC 6080 / API 8090)
│   └── llm_orchestrator/       # LLM-based workflow orchestration
│       ├── llm_base.py         # Provider interface
│       ├── llm_adapters.py     # Provider implementations (Ollama/OpenAI/Anthropic/MetaCentrum/OpenAI-compatible)
│       ├── llm_factory.py      # Provider factory
│       ├── workflow_llm_parser.py    # LLM-facing extraction (parse/classify/extract updates)
│       ├── workflow_parser.py        # Stateful validation/enrichment (WorkflowParser)
│       ├── workflow_definition.py    # Required-parameters-by-step, defaults
│       ├── workflow_state_manager.py # Revisioned checkpoints, undo/restore
│       ├── orchestrator.py     # Workflow executor (WorkflowOrchestrator)
│       ├── interactive_session.py    # CLI interactive session loop
│       ├── rerun_planner.py    # Partial-rerun invalidation planning
│       ├── job_comparator.py   # Cross-job binding-affinity comparison
│       └── job_cleanup.py      # Opt-in job-directory retention/cleanup
│
├── ui/                          # Streamlit web UI (persistent sessions: redock, history/undo, save-as; see docs/GUI_GUIDE.md)
│   ├── app.py                  # Main Streamlit entry point
│   ├── core/                   # Framework-free GUI logic on a GuiSession: state, form, run_control, sessions, chat, box_picker, save_results, results_view (Phase 16)
│   ├── session_state.py        # Streamlit adapter: st.session_state as a GuiSession view, init
│   ├── worker_cap.py           # docker-compose.yml-aware CPU cap + per-algorithm benchmarked default workers
│   ├── orchestrator_bridge.py  # Background-thread workflow execution, per-run log sink (no stdout capture)
│   ├── job_history.py          # Sidebar job history scan, job naming/rename
│   ├── ollama_models.py        # Ollama model listing
│   ├── pymol_client.py         # pymol-interactive helper-API client (box selection only)
│   ├── mol_viewer.py           # Python wrapper for the 3Dmol.js viewer/box-picker component
│   ├── components/mol_viewer/  # Vendored 3Dmol.js + vanilla-JS Streamlit component (no build step)
│   ├── results.py              # Results/comparison rendering
│   └── settings.py             # Persisted LLM provider settings
│
├── installer/                   # Standalone PyInstaller installer
│   ├── install.py              # Installer entry point (Docker check/install, build, launch)
│   └── build.sh                # Freezes install.py into a single-file executable
│
├── tests/                       # Unit and integration tests
│   ├── fixtures/               # Test data (benzene.sdf, water.sdf, ...)
│   ├── test_*.py               # Per-module/service tests
│   ├── test_integration.py     # End-to-end integration tests
│   └── test_docker_integration.py  # Tests against live Docker services
│
├── docker/                      # Docker configurations
│   ├── base.Dockerfile         # Base image with common dependencies + non-root user
│   ├── rcsb_downloader.Dockerfile
│   ├── ligand_preparer.Dockerfile
│   ├── protein_preparer.Dockerfile
│   ├── moldina_runner.Dockerfile
│   ├── pymol_visualizer.Dockerfile
│   ├── pymol_interactive.Dockerfile
│   ├── pymol_interactive_start.sh  # Xvfb/x11vnc/openbox/PyMOL startup script
│   └── pymol_rpc_server.py     # PyMOL XML-RPC server started via `pymol -r`
│
├── docs/                        # Documentation
│   ├── API.md                  # MCP tool specifications
│   ├── GUI_GUIDE.md            # Streamlit UI guide (3Dmol.js viewer, visual box picker)
│   ├── INSTALLATION.md         # Installation guide
│   ├── DOCKER_DEPLOYMENT.md    # Docker deployment guide (incl. security model)
│   ├── LLM_ORCHESTRATION.md    # Natural language interface guide
│   ├── DOCKING_PARAMETERS.md   # Docking parameters reference
│   ├── PYMOL_VISUALIZATION.md  # Legacy/optional PyMOL Visualizer service (not used by default)
│   ├── STATE_PERSISTENCE_STRATEGY.md  # Checkpoint/undo persistence design (CLI + GUI)
│   ├── VERBOSITY_LEVELS.md     # Verbosity modes guide
│   └── *.md                    # Additional documentation
│
├── Moldina/                     # Moldina executable
│   ├── moldina                 # PSO-based docking binary (prebuilt, committed)
│   └── README.md               # Binary provenance, source, update instructions
│
├── cache/                       # Downloaded structures cache (gitignored)
├── demo_output/                 # Example workflow outputs (gitignored)
├── orchestrator_output/         # LLM orchestrator outputs (gitignored, use `cleanup-jobs` to prune)
│
├── nlp_orchestrator.py          # CLI for natural language workflows (also: compare, cleanup-jobs)
├── benchmark_docking.py         # Sequential vs. parallel docking benchmark script
├── benchmark_gui.py             # GUI container CPU/memory sampler (docs/GUI_GUIDE.md "Performance")
├── benchmark_gui_latency.js     # Browser-console GUI interaction-latency snippet
├── benchmark_gui_browser.py     # Headless-browser (Playwright) GUI timing driver
├── example_complete_workflow.py # Example Python API usage
├── example_prepare_ligand.py    # Example ligand preparation
│
├── docker-compose.yml           # Multi-service Docker orchestration
├── Dockerfile.ui                # Streamlit UI image
├── docker_build.ps1/.sh         # Docker build scripts (incl. ui image)
├── docking-platform-installer.spec  # PyInstaller-generated (not committed, see installer/build.sh)
│
├── requirements.txt             # Python dependencies
├── pytest.ini                   # Pytest configuration
├── .env.example                 # Environment variable reference
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
- **In-Browser 3Dmol.js Viewer** - Client-side pose viewing and a visual docking-box picker, no server-side rendering.
- **Cross-Platform** - Works across Windows and Linux path styles.
- **Caching** - Reuses downloaded structures to reduce repeated work.
- **MCP Protocol** - Standard JSON-RPC interface across services.

## Important Notes

### Security: Single-Machine Use Only
- No service in this platform has authentication — every port is only safe to run on a **trusted single machine**
- `docker-compose.yml` binds all service ports to `127.0.0.1` for this reason; do not change this to expose ports on the network without adding auth first
- See [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md#production-deployment) for details and what's required before any shared/remote deployment

### Python Version
- **Use `python3` command** (Python 3.12+) when `python` points to an incompatible interpreter
- System `python` may be Python 2.7 (not compatible)
- Check: `python3 --version` should show 3.9+

### Windows Users
- Use PowerShell (not CMD)
- Set UTF-8 encoding: `$env:PYTHONIOENCODING="utf-8"`
- Use `python3` not `python`

### Viewing Results
- The GUI's in-browser 3Dmol.js viewer needs no PyMOL, no VNC, and no
  server-side rendering — see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)
- Download docked poses as PDBQT (native), PDB, SDF, or MOL2 from the same
  panel
- The legacy `pymol_visualizer` service (`.pml`/`.pse`/`.png`) still works if
  called directly, but isn't part of the default workflow anymore — see
  [docs/PYMOL_VISUALIZATION.md](docs/PYMOL_VISUALIZATION.md)

### Job Output Cleanup
`orchestrator_output/` is never pruned automatically. To list (dry run) or delete old jobs:
```bash
# List jobs older than 30 days without deleting anything
python nlp_orchestrator.py cleanup-jobs --older-than-days 30

# Keep only the 20 most recent jobs, actually delete the rest
python nlp_orchestrator.py cleanup-jobs --keep-last 20 --yes
```

## Documentation

- **[docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)** - Streamlit UI guide: sessions/redock, revision history + undo, saved result bundles, 3Dmol.js viewer, visual docking-box picker, download formats
- **[docs/VERBOSITY_LEVELS.md](docs/VERBOSITY_LEVELS.md)** - Guide to verbosity levels (`low`, `normal`, `mcp_demo`)
- **[docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md)** - Natural language interface guide and examples
- **[docs/DOCKING_PARAMETERS.md](docs/DOCKING_PARAMETERS.md)** - Comprehensive guide to all docking parameters across all stages
- **[docs/API.md](docs/API.md)** - Complete MCP tool specifications and JSON-RPC examples
- **[docs/PYMOL_INTERACTIVE.md](docs/PYMOL_INTERACTIVE.md)** - Browser-based PyMOL interactive service and docking box helper API (CLI box selection; the GUI has its own visual picker)
- **[docs/PYMOL_VISUALIZATION.md](docs/PYMOL_VISUALIZATION.md)** - Legacy/optional PyMOL Visualizer service (not used by default)
- **[docs/INSTALLATION.md](docs/INSTALLATION.md)** - Installation instructions (Docker and local)
- **[docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)** - Docker deployment guide

## License

MIT License
