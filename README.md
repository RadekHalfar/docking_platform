# MCP-Based Docking System

A fully containerized molecular docking pipeline using Model Context Protocol (MCP) to coordinate protein preparation, ligand preparation, Moldina docking (PSO or classic Vina search), and an in-browser 3Dmol.js visualization.

**Zero Installation Required:** Everything runs in Docker containers, so you do not need to install Moldina, RDKit, or other dependencies locally.

**Natural Language Interface (optional):** Control the workflow with text commands through a pluggable LLM layer: a cloud provider with an API key, or a local Ollama. The GUI's forms work without any LLM.

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
4. **Moldina Runner** - Executes Moldina docking: its PSO search (default) or the classic Vina search, chosen per run.
5. **GUI 3D Viewer** - In-browser 3Dmol.js viewer + visual docking-box picker (no server-side rendering; see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)).
6. **LLM Orchestrator** - Provides natural-language workflow orchestration via pluggable LLM providers (none configured by default; cloud or local Ollama).

All services run in Docker containers.

## Quick Start

### Docker Deployment (Recommended - 5 Minutes!)

**Prerequisites:** Docker Desktop (Windows/Mac) or Docker Engine (Linux)

```bash
# 1. Build all Docker images (~5-10 minutes first time)
.\docker_build.ps1      # Windows PowerShell
# ./docker_build.sh     # Linux/Mac

# 2. Start all services (~30 seconds; about 2.2 GB of images, no LLM)
docker compose up -d

# 3. Check services are healthy
docker compose ps

# 4. Open the GUI and dock from its forms -- no LLM needed:
#    http://localhost:3000
#    The results tab shows the poses in an in-browser 3Dmol.js viewer, with
#    PDBQT/PDB/SDF/MOL2 downloads.

# 5. Optional: an LLM for chat and the natural-language CLI
#    (see "Choose an LLM" below)

# 6. Optional: run full tests in Docker
docker compose --profile dev run --rm -e SERVICE_HOST=host.docker.internal script-runner python -m pytest tests/ --tb=short
# GUI (NiceGUI) tests need the ui image instead (see docs/DOCKER_DEPLOYMENT.md#gui-tests):
docker compose run --rm --no-deps ui python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py tests/test_ui_comparison.py tests/test_ui_results_rendering.py
```

**See [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md) for the detailed guide.**

### Choose an LLM (optional)

No LLM is configured by default. The GUI's forms, **Run Docking**, the
results viewer and chat commands such as `run`, `use vina`, `history` and
`save results as <name>` all work without one. Only natural-language parsing
needs an LLM: free text in the GUI chat, and the CLI's commands.

Pick one in the GUI's **Settings** (the gear icon, top right), or in `.env`
for the CLI (see [.env.example](.env.example)):

| Option | Download | Notes |
|---|---|---|
| **OpenAI / Anthropic / MetaCentrum** | none | Needs an API key (MetaCentrum: an e-INFRA CZ token). Tick **Remember API key** to keep it across restarts. |
| **Local Ollama** (opt-in container) | ~4.8 GB image + a model | Private and free; runs on the CPU in Docker. |
| **Ollama app on the host** | Ollama installer + a model | Uses the GPU (Apple Metal on Mac). Endpoint `http://host.docker.internal:11434`. |
| **Other OpenAI-compatible server** | depends | LM Studio, vLLM, Groq and similar. |

**Local Ollama in Docker:**

```powershell
# Start the opt-in Ollama container (downloads the ~4.8 GB image once)
docker compose --profile local-llm up -d ollama

```

Then pick **Ollama (local)** in the GUI's Settings. The dialog finds the
running Ollama (the container, or the Ollama app on the host), offers the two
recommended models (the first two below) with their sizes, and downloads the one you pick
with a progress bar; select it and press **Apply settings**. Models persist
in the `ollama-data` volume. Without the GUI, download one with
`docker exec -it mcp-llm ollama pull gemma3:4b`. For the CLI, pass the
provider and model:

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --llm-provider ollama --llm-model gemma3:4b "Download 6lu7 and ATP, prepare and dock"
```

| Model | Download Size | Notes |
|-------|---|---|
| **Gemma 3 4B** (`gemma3:4b`) | 3.3 GB | Lighter; fine for 8–16 GB RAM |
| **Qwen 2.5 Coder 7B Q4** (`qwen2.5-coder:7b-instruct-q4_K_M`) | 4.7 GB | Strong instruction following; 16 GB RAM recommended |
| **Mistral 7B Q4** (`mistral`) | 4.0 GB | Balanced |
| **Phi-3 Mini Q4** (`phi3`) | 2.0 GB | Fastest, lowest memory |

Switching models needs no rebuild: download another model (any name from the
Ollama library can be typed into the dialog's **Download a model** field —
press Enter after typing it, then **Download**) and select it, or pass `--llm-model`. `docker exec -it mcp-llm ollama list` shows what's downloaded.

**See [docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md) for the natural-language interface, providers and troubleshooting.**

### Natural-Language CLI

With an LLM configured:

```powershell
# Check everything is healthy (no local Python setup required)
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health

# Run a complete workflow with natural language
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download protein 6lu7 and ligand ATP, prepare them, and dock with center at 10,20,30 and box size 25,30,35"

# Verbosity options (default is 'low' for clean output):
# --verbose low        # Minimal output (default)
# --verbose normal     # Show service logs
# --verbose mcp_demo   # Full MCP JSON-RPC demonstration

# Results automatically saved to ./orchestrator_output/<job_id>/

# A missing docking center/box_size is asked for, and the LLM parses it
# from your free-text answer; --clarification-mode legacy uses fixed prompts.
```

### Local Installation (Optional)

**Note:** Docker is recommended. Local installation only needed for development.

**See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed instructions.**

```bash
# Python 3.10+ required (not Python 2.7!); the Docker images use 3.12
conda create -n docking-platform python=3.12
conda activate docking-platform
conda install -c conda-forge rdkit openbabel meeko
pip install -r requirements.txt

# Start core API services individually (ports 8001-8004)
python3 -m services.rcsb_downloader.server
python3 -m services.ligand_preparer.server
python3 -m services.protein_preparer.server
python3 -m services.moldina_runner.server
```

### Docker Deployment Details

**No installation required!** All dependencies (Moldina, RDKit, OpenBabel, Meeko) are included in containers.

**Services:**
- RCSB Downloader (port 8001)
- Ligand Preparer (port 8002)
- Protein Preparer (port 8003)
- Moldina Runner (port 8004), with the prebuilt Moldina executable committed under `Moldina/`
- Web GUI (port 3000)

All five images share one ~2 GB base layer, so together they take about
2.2 GB of disk (measured with `docker system df -v`).

**See [docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md) for complete Docker guide.**

## Example Workflow

### Using Natural Language

These need an LLM (see [Choose an LLM](#choose-an-llm-optional)); set it in `.env`
or add `--llm-provider`/`--llm-model`.

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

# Example output (abridged):
# Workflow Summary:
# ==================================================
# Services: get_input_files, prepare_ligands, prepare_protein, dock, visualize
#
# Parameters:
#   Protein: 6lu7
#   Ligands: ATP
#   Docking Center: [10.0, 20.0, 30.0]
#   Box Size: [25.0, 25.0, 25.0]
#
# Executing workflow...
# 📥 STEP 1: Getting input files...
# ...
# WORKFLOW RESULTS
# Job ID: job_20261029_070450_7a1a17
# Status: completed
#
# A missing value (e.g. the box center) is first asked for in a short
# follow-up conversation; see --clarification-mode in docs/LLM_ORCHESTRATION.md.
# Results land in ./orchestrator_output/<job_id>/.
```

### Using Python API

`example_complete_workflow.py` calls each MCP service directly (bypassing the
orchestrator) — it's a standalone demo of the raw service APIs. Its last step
calls `convert_docked_poses`, the same tool behind the workflow's `visualize`
step (see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)).

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
# ✅ Converted 2 ligand(s): ATP_poses.sdf, HEM_poses.sdf
```

**Results in `./demo_output/`:**
- `downloaded/` - Raw PDB/SDF files
- `prepared_proteins/` - PDBQT receptor files
- `prepared_ligands/` - PDBQT ligand files
- `docking_results/` - Docked poses from Moldina
- `visualization/` - Docked poses as SDF/PDB/MOL2

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
│   ├── schemas.py              # MCP tool-definition models
│   └── utils.py                # Validation, checksums, misc helpers
│
├── services/                    # MCP service implementations
│   ├── rcsb_downloader/        # Protein/ligand download service (port 8001)
│   ├── ligand_preparer/        # Ligand prep + docked-pose->PDB/SDF/MOL2 conversion (port 8002)
│   ├── protein_preparer/       # Protein preparation service (port 8003)
│   ├── moldina_runner/         # Molecular docking service (port 8004)
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
├── ui/                          # GUI logic and helpers, framework-free (the NiceGUI app is ui_nice/; see docs/GUI_GUIDE.md)
│   ├── core/                   # GUI logic on a GuiSession: state, form, uploads, run_control, sessions, chat, box_picker, save_results, results_view, llm_settings (Phase 16)
│   ├── worker_cap.py           # docker-compose.yml-aware CPU cap + per-algorithm benchmarked default workers
│   ├── orchestrator_bridge.py  # Background-thread workflow execution, per-run log sink (no stdout capture)
│   ├── job_history.py          # Session list scan, job naming/rename
│   ├── ollama_models.py        # Ollama model listing
│   ├── components/mol_viewer/vendor/  # Vendored 3Dmol.js, served to the viewer element (no build step)
│   ├── results.py              # Results/comparison rendering
│   └── settings.py             # Persisted LLM provider settings, LLM client builder
│
├── ui_nice/                     # The web GUI (NiceGUI, port 3000; persistent sessions: redock, history/undo, save-as)
│   ├── app.py                  # Entry point: the page (one GuiSession per client), /health, /files downloads
│   ├── layout.py               # Header, settings dialog, chat drawer, Setup/Results/Sessions tabs
│   ├── controller.py           # Per-client page controller: progress timer, sync of the views
│   ├── setup_tab.py            # Setup tab: inputs, Docking Box, Advanced, actions, redock dialog, progress
│   ├── chat_drawer.py          # Chat drawer: messages, Run log, input (LLM parse via run.io_bound)
│   ├── box_picker_panel.py     # Setup tab's right side: the visual box picker
│   ├── results_tab.py          # Results tab: viewer + ligand/pose grids, save-as, Session Log, downloads, compare
│   ├── sessions_tab.py         # Sessions tab: revision history + Restore, session lists (Load/Rename/Delete/Compare)
│   ├── settings_dialog.py      # Settings dialog: LLM provider, endpoint, model (Ollama list), API key
│   ├── mol_viewer.py           # 3Dmol.js viewer element (camera kept; box/poses redrawn in place)
│   ├── mol_viewer.js           # Its Vue component (3Dmol.js scene, box overlay, poses)
│   └── pytest.ini              # GUI test tier config (NiceGUI User fixture)
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
│   └── moldina_runner.Dockerfile
│
├── docs/                        # Documentation
│   ├── API.md                  # MCP tool specifications
│   ├── GUI_GUIDE.md            # Web GUI guide (3Dmol.js viewer, visual box picker)
│   ├── INSTALLATION.md         # Installation guide
│   ├── DOCKER_DEPLOYMENT.md    # Docker deployment guide (incl. security model)
│   ├── LLM_ORCHESTRATION.md    # Natural language interface guide
│   ├── DOCKING_PARAMETERS.md   # Docking parameters reference
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
├── Dockerfile.ui                # GUI image (NiceGUI, non-root appuser)
├── docker_build.ps1/.sh         # Docker build scripts
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

# Expected: ~1,300 tests passing (a few skip without RDKit or live services)
```

## Key Features

- **Natural Language Control** - Command workflows with plain text.
- **Three Verbosity Levels** - Choose minimal, detailed, or protocol-level output.
- **Modular LLM Layer** - Provider interface + adapters (no provider by default; Ollama, OpenAI, Anthropic, MetaCentrum, OpenAI-compatible) with runtime model selection.
- **Containerized Deployment** - Run all dependencies in Docker.
- **Moldina Docking** - PSO search (default, fast) or the classic Vina search, selectable per run.
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
- **Use `python3` command** (Python 3.10+; Docker and CI use 3.12) when `python` points to an incompatible interpreter
- System `python` may be Python 2.7 (not compatible)
- Check: `python3 --version` should show 3.10+

### Windows Users
- Use PowerShell (not CMD)
- Set UTF-8 encoding: `$env:PYTHONIOENCODING="utf-8"`
- Use `python3` not `python`

### Viewing Results
- The GUI's in-browser 3Dmol.js viewer needs no desktop viewer and no
  server-side rendering — see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)
- Download docked poses as PDBQT (native), PDB, SDF, or MOL2 from the same
  panel

### Job Output Cleanup
`orchestrator_output/` is never pruned automatically. To list (dry run) or delete old jobs:
```bash
# List jobs older than 30 days without deleting anything
python nlp_orchestrator.py cleanup-jobs --older-than-days 30

# Keep only the 20 most recent jobs, actually delete the rest
python nlp_orchestrator.py cleanup-jobs --keep-last 20 --yes
```

## Documentation

- **[docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)** - Web GUI guide: sessions/redock, revision history + undo, saved result bundles, 3Dmol.js viewer, visual docking-box picker, download formats
- **[docs/VERBOSITY_LEVELS.md](docs/VERBOSITY_LEVELS.md)** - Guide to verbosity levels (`low`, `normal`, `mcp_demo`)
- **[docs/LLM_ORCHESTRATION.md](docs/LLM_ORCHESTRATION.md)** - Natural language interface guide and examples
- **[docs/DOCKING_PARAMETERS.md](docs/DOCKING_PARAMETERS.md)** - Comprehensive guide to all docking parameters across all stages
- **[docs/API.md](docs/API.md)** - Complete MCP tool specifications and JSON-RPC examples
- **[docs/INSTALLATION.md](docs/INSTALLATION.md)** - Installation instructions (Docker and local)
- **[docs/DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)** - Docker deployment guide

## License

MIT License
