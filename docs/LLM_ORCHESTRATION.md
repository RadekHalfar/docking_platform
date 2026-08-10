# LLM Workflow Orchestration

Natural-language interface for molecular docking workflows using modular LLM providers and MCP services.

---

## Overview

This system lets you control the full molecular docking pipeline with plain-language commands. A provider-configured LLM parses each request, extracts parameters, and orchestrates calls across MCP services.

### Key Features

- **Natural Language Input** - Describe workflows in plain English.
- **Modular LLM Architecture** - Provider interface + adapters (default: Ollama).
- **Runtime Model Switching** - Change model with flags/env vars, no rebuild required.
- **MCP Integration** - Coordinates all services through a single workflow.
- **Smart Defaults** - Applies defaults when optional parameters are omitted.
- **Validation** - Checks required parameters and prompts for missing values.
- **Docker Deployment** - Runs with a single Docker command.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    User Input                       │
│  "Download ATP, HEM and protein 6lu7, prepare      │
│   and dock with center 10,20,30 and box 25,30,35"  │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│      LLM Provider Adapter (default: Ollama)         │
│  - Provider interface (BaseLLMClient)               │
│  - Adapter implementation (OllamaAdapter)           │
│  - Factory-based selection (create_llm_client)      │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│           Workflow Parser & Validator               │
│  - Validates parameters                             │
│  - Applies defaults                                 │
│  - Checks for missing required params               │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│          Workflow Orchestrator                      │
│  - Sequences MCP service calls                      │
│  - Manages file paths                               │
│  - Handles errors                                   │
└──────┬──────┬──────┬──────┬──────────────────────────┘
       │      │      │      │
       ▼      ▼      ▼      ▼      ▼
    RCSB  Ligand Protein Moldina Ligand
    Down   Prep   Prep   Docking Prep (poses)
```

  ### Centralized Workflow Metadata (Phase 2)

  Workflow structure is now defined in one place: [services/llm_orchestrator/workflow_definition.py](../services/llm_orchestrator/workflow_definition.py).

  This shared module provides:
  1. Canonical step order
  2. Required parameters per step
  3. Default workflow parameters
  4. Parameter invalidation map for partial reruns
  5. MCP service endpoint defaults and env-var overrides

  `WorkflowParser`, `rerun_planner`, and `WorkflowOrchestrator` now consume this metadata directly.

---

## Quick Start

### 1. Start All Services

```bash
# Start Docker services including Ollama
docker compose up -d

# Wait for Ollama to start (~30 seconds)
docker ps
```

### 2. Download LLM Model

```bash
# Download Qwen 2.5 Coder 7B Q4 (default, ~4.7GB)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# OR download DeepSeek Coder 6.7B Q4 (alternative, ~4.1GB)
docker exec -it mcp-llm ollama pull deepseek-coder:6.7b-instruct-q4_K_M

# OR download Mistral (alternative, ~4GB)
docker exec -it mcp-llm ollama pull mistral

# OR download Phi-3 (smaller, faster, ~2GB)
docker exec -it mcp-llm ollama pull phi3
```

### 3. Check Service Health

```bash
python nlp_orchestrator.py --check-health
```

### 4. Optional: Select Provider/Model at Runtime

```bash
# Defaults: --llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model qwen2.5-coder:7b-instruct-q4_K_M
python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP"

# Use Qwen 2.5 Coder 7B Q4
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP"

# Explicit provider and endpoint
python nlp_orchestrator.py --llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model phi3 "Download 6lu7"
```

### 5. Run Your First Workflow

```bash
python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them for docking and perform docking with center 10,20,30 and box size 25,30,35"
```

---

## Usage

### Command-Line Mode

**Basic usage:**
```bash
python nlp_orchestrator.py "YOUR_NATURAL_LANGUAGE_COMMAND"
```

**Examples:**

```bash
# Simple download
python nlp_orchestrator.py "Download protein 6lu7"

# Download and prepare
python nlp_orchestrator.py "Download ATP and 6lu7, then prepare them"

# Full workflow
python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them for docking and dock with center 10,20,30 and box size 25,30,35"

# With custom parameters
python nlp_orchestrator.py "Download 1ABC and ligand NAD, prepare at pH 5.0, dock with exhaustiveness 16"
```

### Local File Input

Use `--protein-file` and/or `--ligand-files` to dock against local structures instead of
(or alongside) RCSB downloads. Whichever side has a local file is copied straight into the
job's `downloads/` directory and skips the RCSB download for that side; the other side, if
not given a local file, is still downloaded from RCSB normally.

```bash
# Local protein file, ligand still downloaded from RCSB
python nlp_orchestrator.py --protein-file /data/6lu7.pdb \
  "prepare and dock ATP, center 10 20 30 box 25 30 35"

# Local ligand file(s), protein still downloaded from RCSB
# (repeat --ligand-files once per file; it takes a single path each time)
python nlp_orchestrator.py --ligand-files /data/ATP.sdf --ligand-files /data/HEM.sdf \
  "download 6lu7, prepare and dock, center 10 20 30 box 25 30 35"

# Both local — no RCSB download at all
python nlp_orchestrator.py --protein-file /data/6lu7.pdb --ligand-files /data/ATP.sdf \
  "prepare and dock, center 10 20 30 box 25 30 35"
```

Accepted protein extensions: `.pdb`, `.cif`. Accepted ligand extensions: `.sdf`, `.mol`,
`.mol2`, `.pdbqt`. An invalid extension or a path that doesn't exist is reported as a
missing/invalid parameter the same way an unresolvable PDB ID would be.

A single `.sdf` path given to `--ligand-files` may itself contain multiple molecules
(`$$$$`-delimited records) — all of them are docked, not just the first. See
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files) for naming and
partial-failure behavior.

These flags also work with `--interactive` mode and apply to every `new_workflow` request
issued in that session.

For docking one protein against every ligand file in a folder, see
[Batch Docking from a Folder](BATCH_DOCKING.md) (`--ligand-folder`).

### Interactive Mode

**CLI only.** Everything in this section — session state, parameter
updates, `rerun`, revision history/`undo`, and named result bundles — is
implemented in `nlp_orchestrator.py`'s interactive terminal session. The
Streamlit web UI (`ui/`) does not wire in `WorkflowStateManager` and has
none of these capabilities; it only runs one-shot workflows. See
[docs/STATE_PERSISTENCE_STRATEGY.md](STATE_PERSISTENCE_STRATEGY.md) for the
persistence layer these features are built on.

```bash
python nlp_orchestrator.py --interactive
```

Then type commands interactively:
```
🧬 > Download 6lu7 and ATP, prepare and dock
🧬 > state
🧬 > change ligand to HEM
🧬 > set center to 10, 20, 30
🧬 > save results as hem_box_a
🧬 > show history
🧬 > new Download 6lu7 and ADP, prepare and dock
🧬 > end session
```

#### Session behavior (Phase 1 + Phase 2)

1. First natural-language request starts the active session job.
2. After completion the CLI blocks and waits for a follow-up command.
3. Use `state` to inspect the active job (disk-backed parameters).
4. Use `new <request>` to explicitly start a fresh workflow in the same session.
5. Use `end`/`quit`/`exit` or "end session" to stop.

#### Current command granularity

For now, pass one intent per user input line.

Examples:
- ✅ `state`
- ✅ `save results as hem_box_a`
- ❌ `state and save results as hem_box_a`

When one input contains multiple intents, only one intent is dispatched in the current implementation.
Planned improvement: `dev_plan.md` Phase 10 adds ordered multi-intent command sequencing.

#### Intent classification for follow-up commands (Phase 2)

When a job is active, every command is classified into one of these intents before dispatch:

| Intent | Example inputs | Behavior |
|---|---|---|
| `show_state` | `state`, `show state` | Print disk-backed parameters and status |
| `show_history` | `history`, `show history` | Print revision history table |
| `end_session` | `end`, `end session`, `quit session` | Exit the session cleanly |
| `new_workflow` | `new <request>` | Start a fresh workflow |
| `update_parameters` | `change ligand to HEM`, `set center to 10,20,30` | Extract updates via LLM, persist to disk, invalidate affected services |
| `save_results` | `save results as hem_box_a` | Save named result bundle from current docking/visualization outputs |
| `undo_revision` | `undo 14` | Restore parameters/state to a prior revision |
| `rerun_steps` | `rerun`, `dock`, `rerun docking` | Re-execute invalidated steps with pre-flight validation |
| `use_pymol_box` | `use pymol box` | *(Phase 6 — stub active)* |
| `help` | `help`, `?` | Print command reference |

Common keywords (`state`, `history`, `end session`, `save results as <name>`, `undo N`, `new <request>`) are resolved deterministically without an LLM call.
Ambiguous free-text commands (e.g. "change ligand to HEM") are sent to the LLM for classification.

#### Update parameters

After classifying `update_parameters`, the system:
1. Calls the LLM to extract the concrete field → value mapping.
2. Merges the changes with the current persisted parameters.
3. Saves a new checkpoint revision to disk.
4. Immediately removes invalidated services from the executed set (e.g. changing `center` invalidates `dock` and `visualize`).
5. Prints a confirmation with a hint to `rerun`.

#### Rerun steps

`rerun`, `dock`, or `rerun docking` triggers a partial re-execution:

1. Required parameters for all planned steps are validated before the plan is shown.
2. Only steps not yet successfully executed (or invalidated by a parameter change) are included.
3. User confirms the plan before execution starts.
4. On success the executed set is updated; `last_status` returns to `completed`.

`last_status` shows `parameters_changed` whenever parameters have been updated but not yet re-executed.

#### Revision history and undo

`history` prints a table of all checkpoint events for the active job:

```
Revision history for job job_abc123:
    Rev  Timestamp                   Type                    Details
  -------------------------------------------------------------------------------
      1  2026-04-24T10:00:00.000...  parameters_update       source=initial_parse  [params]
      2  2026-04-24T10:05:00.000...  state_update            step=dock  [state]
      3  2026-04-24T10:10:00.000...  parameters_update       source=interactive_update  [params]

  Current revision: 3
  Use 'undo <revision>' to restore any prior revision.
```

`undo <revision>` restores that revision's parameters/state to the latest files and creates a new `undo_restore` revision so the action is auditable. After undo all services are marked as needing re-execution; use `rerun` to apply.

#### Save named result bundles

`save results as <name>`, `save docking as <name>`, or `save visualization as <name>` copies the current job artifacts into a named bundle:

```
orchestrator_output/job_<id>/saved_results/<name>/
  metadata.json
  docking_results/        ← scope: docking or all
  visualization/          ← scope: visualization or all
  docking_box.json        ← generated from current parameters
```

**Save is blocked** when parameters have been changed but not yet re-executed (`last_status = parameters_changed`). This prevents saving results that don't match the current parameter state. Run `rerun` first, or `undo` to revert.

Save scope keywords:

| Command | Scope |
|---|---|
| `save results as <name>` | docking + visualization (all) |
| `save docking as <name>` | docking results only |
| `save visualization as <name>` | visualization only |

If the bundle name already exists, the system asks for overwrite confirmation or offers an auto-suffixed name.

Valid bundle names: `[A-Za-z0-9][A-Za-z0-9._-]{0,64}` — no path separators, no `..`, no reserved names.

### Options

```bash
python nlp_orchestrator.py --help

Options:
  --interactive, -i         Run in interactive mode
  --llm-provider NAME       LLM provider name (default: ollama)
  --llm-endpoint URL        LLM API endpoint URL (default: http://localhost:11434)
  --llm-model MODEL         LLM model name (default: qwen2.5-coder:7b-instruct-q4_K_M)
  --llm-api-key KEY         Optional API key for providers that require auth
  --llm-base-url URL        Base URL for openai_compatible servers (e.g. http://localhost:1234/v1 for LM Studio)
  --output-dir DIR          Output directory (default: ./orchestrator_output)
  --protein-file PATH       Local protein .pdb/.cif file instead of downloading from RCSB
  --ligand-files PATH       Local ligand file; repeat the flag for multiple ligands
  --ligand-folder DIR       Directory of local ligand files, scanned recursively (overrides --ligand-files and any ligand IDs in the command)
  --check-health            Check all services and exit
  --list-models             List available models from selected provider
  --verbose {low,normal,mcp_demo}  Verbosity level (default: low)
  --clarification-mode MODE Missing-parameter mode: hybrid (default) or legacy
  --debug                   Enable debug logging
```

Environment variable alternative:

```bash
CLARIFICATION_MODE=legacy python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
```

---

## Natural Language Examples

### What You Can Say

**Download structures:**
- "Download protein 6lu7"
- "Download ligands ATP and HEM"
- "Get protein 1ABC and ligand NAD from RCSB"

**Preparation:**
- "Prepare them for docking"
- "Prepare at pH 7.4"
- "Prepare protein and ligands with hydrogens"

**Docking:**
- "Dock with center 10,20,30 and box size 25,30,35"
- "Run docking with exhaustiveness 16"
- "Dock using 8 CPUs and population 1000"

**Complete workflows:**
- "Download ATP, HEM and protein 6lu7, prepare and dock"
- "Get 1ABC and NAD, prepare them, then dock with center 15,25,35 and box 30,30,30"
- "Download ligands from file ligands.txt and protein 6lu7, prepare at pH 5.0, dock with high exhaustiveness"

### Supported Services

The LLM can identify these workflow stages:

1. **`get_input_files`** - Get protein/ligand structures (downloaded from RCSB, or copied from local files)
2. **`prepare_ligands`** - Prepare ligands for docking
3. **`prepare_protein`** - Prepare protein for docking
4. **`dock`** - Run molecular docking
5. **`visualize`** - Convert docked poses to PDB/SDF/MOL2 for the GUI's in-browser 3Dmol.js viewer (see [GUI_GUIDE.md](GUI_GUIDE.md))

### Supported Parameters

**Structure selection:**
- `protein_id` - PDB ID (e.g., "6lu7", "1ABC")
- `ligand_ids` - List of ligands (e.g., "ATP", "HEM", "NAD")

**Preparation:**
- `ph` - pH value (default: 7.4)
- `add_hydrogens` - Add hydrogens (default: true)
- `remove_waters` - Remove waters (default: true)

**Docking (critical):**
- `center` - Docking box center [x, y, z]
- `box_size` - Box dimensions [x, y, z] in Angstroms
- `exhaustiveness` - Search thoroughness (default: 8)
- `cpu` - Number of CPUs (default: 1)
- `population` - PSO population size (default: 500)

**Visualization:**
- `formats` - Which pose formats to generate for the GUI's 3Dmol.js viewer/downloads: any of `sdf`, `pdb`, `mol2` (default: all three; PDBQT is always available natively)

---

## Hybrid Clarification Parser

When required fields are missing or invalid, clarification runs in one of two modes:

- `hybrid` (default): LLM + deterministic safety checks
- `legacy`: deterministic prompts only (pre-hybrid behavior)

### What Hybrid Is

Hybrid clarification keeps validation deterministic, but uses the LLM to improve interaction quality:

1. Parse request with LLM and validate against required schema.
2. Ask one short follow-up for one missing/invalid field.
3. Convert the user answer into strict JSON parameter updates.
4. Re-validate the whole workflow after every turn.
5. Repeat until complete or clarification limit is reached.

### What Hybrid Does

- Uses LLM to generate concise clarification questions.
- Uses LLM to extract structured updates (JSON only).
- Uses LLM to classify docking-box source intent (`manual` vs `pymol`) from natural text.
- Falls back to deterministic menu selection if box-source intent is ambiguous.
- In manual box mode, parses `center` and `box_size` from natural-language answers with LLM.
- Never bypasses validation; updates must satisfy required parameter rules.

### Failure Safety

- If an answer cannot be parsed into valid updates, user is prompted to try again.
- Clarification is bounded (`max_turns=8` in hybrid flow).
- If required fields are still unresolved after the loop, workflow execution is stopped.

### How To Use Hybrid

Hybrid is default, so no flag is required:

```bash
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
```

Explicitly request hybrid:

```bash
python nlp_orchestrator.py --clarification-mode hybrid "Download 6lu7 and ATP, prepare and dock"
```

Example (missing docking box):

```text
How should we define docking box coordinates? (manually define coordinates or interactive PyMOL): I'll type them.
Please provide docking box center as x,y,z: center 10,20,30 and box size 25,30,35
```

### How To Use Legacy Mode

Use legacy mode when you want deterministic prompts only:

```bash
python nlp_orchestrator.py --clarification-mode legacy "Download 6lu7 and ATP, prepare and dock"
```

Or via environment variable:

```bash
CLARIFICATION_MODE=legacy python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
```

Legacy behavior characteristics:

- No LLM follow-up question generation for missing fields.
- No LLM JSON extraction from clarification replies.
- Docking box path uses deterministic menu/default prompts.

---

## Output

Each workflow execution creates a job directory:

```
orchestrator_output/
└── job_1730304123/
    ├── downloads/
    │   ├── 6lu7.pdb
    │   ├── ATP.sdf
    │   └── HEM.sdf
    ├── prepared/
    │   ├── 6lu7.pdbqt
    │   ├── ATP.pdbqt
    │   └── HEM.pdbqt
    ├── docking_results/
    │   ├── ATP_out.pdbqt
    │   ├── HEM_out.pdbqt
    │   └── moldina_config.txt
    ├── visualization/
    │   ├── ATP_poses.sdf
    │   ├── ATP_poses.pdb
    │   └── ATP_poses.mol2
    ├── saved_results/
    │   ├── atp_box1/
    │   │   ├── metadata.json
    │   │   ├── docking_results/
    │   │   ├── visualization/
    │   │   └── docking_box.json
    │   └── hem_trial_b/
    │       ├── metadata.json
    │       └── docking_results/
    └── workflow_results.json
```

**workflow_results.json** contains:
```json
{
  "job_id": "job_1730304123",
  "job_name": "",
  "status": "completed",
  "services_executed": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
  "results": {
    "get_input_files": {...},
    "prepare_ligands": {...},
    "dock": {...}
  },
  "errors": []
}
```

`job_name` is an optional, non-unique display label — `job_id` remains the
sole unique identifier and is unaffected by it. It defaults to `""` when not
supplied. The GUI sets it from the **Job name** field at submission time (or
via the sidebar's **Rename** button afterward — see
[docs/GUI_GUIDE.md](GUI_GUIDE.md#naming-a-job)); the CLI (`nlp_orchestrator.py`)
does not currently set it, so CLI-run jobs always persist `"job_name": ""`
unless `execute_workflow(..., job_name=...)` is called directly.

---

## Hardware Requirements

### Minimum

- **RAM:** 8 GB (for small models like Phi-3)
- **Storage:** 10 GB (for model + data)
- **GPU:** Optional (CPU-only works but slower)

### Recommended

- **RAM:** 16+ GB
- **Storage:** 20+ GB
- **GPU:** NVIDIA GPU with 6+ GB VRAM (for Llama 2, Mistral)

### Optimal

- **RAM:** 32+ GB (your system!)
- **GPU:** NVIDIA GPU with 12+ GB VRAM
- **Storage:** 50+ GB

**Your system (40GB RAM + NVIDIA GPU) is ideal for running large models like Llama 2 or Mistral with fast inference!**

---

## Model Selection

### Available Models

| Model | Size | Speed | Quality | Use Case |
|-------|------|-------|---------|----------|
| **qwen2.5-coder:7b-instruct-q4_K_M** | ~4.7GB | Medium | High | Project default |
| **deepseek-coder:6.7b-instruct-q4_K_M** | ~4.1GB | Medium | High | Strong alternative |
| **llama2** | ~4GB | Medium | High | Stable alternative |
| **mistral** | ~4GB | Fast | High | Fast alternative |
| **phi3** | ~2GB | Very Fast | Good | Resource-constrained systems |
| **codellama** | ~4GB | Medium | High | Code-focused (alternative) |

### Download Models

```bash
# Qwen 2.5 Coder 7B Q4 (project default)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# DeepSeek Coder 6.7B Q4 (strong alternative)
docker exec -it mcp-llm ollama pull deepseek-coder:6.7b-instruct-q4_K_M

# Mistral (alternative)
docker exec -it mcp-llm ollama pull mistral

# Phi-3 (lightweight)
docker exec -it mcp-llm ollama pull phi3
```

### List Available Models

```bash
python nlp_orchestrator.py --list-models

# OR
docker exec -it mcp-llm ollama list
```

### Use Different Model

```bash
python nlp_orchestrator.py --llm-model mistral "Download 6lu7"

# Qwen 2.5 Coder 7B Q4
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7"
```

### Switch Models Without Rebuild

```bash
# Pull model once
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# Use it immediately (no docker image rebuild)
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP, prepare and dock"
```

---

## Troubleshooting

### Ollama Not Starting

**Check if running:**
```bash
docker ps | grep ollama
```

**View logs:**
```bash
docker logs mcp-llm
```

**Restart:**
```bash
docker compose restart ollama
```

### Model Not Found

**Download the model:**
```bash
docker exec -it mcp-llm ollama pull llama2
```

**Check available models:**
```bash
docker exec -it mcp-llm ollama list
```

### GPU Not Detected

**Check NVIDIA Docker support:**
```bash
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

**If not working:**
- Install NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
- Restart Docker Desktop

### Slow Inference

**Use smaller model:**
```bash
docker exec -it mcp-llm ollama pull phi3
python nlp_orchestrator.py --llm-model phi3 "..."
```

**Check GPU usage:**
```bash
nvidia-smi
```

### LLM Returns Invalid JSON

**Try different model:**
```bash
python nlp_orchestrator.py --llm-model mistral "..."
```

**Enable debug mode:**
```bash
python nlp_orchestrator.py --debug "..."
```

### Empty Docking Coordinates From LLM

Some models may return empty arrays for docking coordinates, for example `center: []` or `box_size: []`.
The orchestrator flags these for clarification (instead of running docking with invalid values) and asks follow-up questions.

Accepted input format at the prompt:
```text
10,20,30
[10,20,30]
```

If parsing fails, safe defaults are applied:
- Center: `[10.0, 20.0, 30.0]`
- Box size: `[25.0, 30.0, 35.0]`

### Invalid Protein ID Clarification

If the LLM extracts an invalid protein ID (for example `6lu`), it is reported as invalid (not missing) and the orchestrator asks for a corrected 4-character PDB ID before continuing.

### Service Connection Errors

**Check all services:**
```bash
python nlp_orchestrator.py --check-health
```

**Restart services:**
```bash
docker compose restart
```

---

## Best Practices

### Do

- **Be specific** with protein and ligand IDs
- **Provide docking parameters** (center, box_size) when possible
- **Check service health** before running workflows
- **Use appropriate model** for your hardware
- **Review workflow summary** before execution
- **Keep Ollama updated** for best performance

### Do Not

- **Use vague descriptions** ("dock some ligands")
- **Omit critical parameters** without being prepared to enter them
- **Run multiple heavy workflows** simultaneously
- **Use CPU-only** if you have a GPU (much slower)
- **Ignore error messages** (they contain useful info)

---

## API Integration

### Python API

You can use the orchestrator programmatically:

```python
from services.llm_orchestrator import (
    WorkflowOrchestrator, OllamaAdapter, WorkflowParser, parse_workflow_with_llm,
)

# Initialize components
llm_client = OllamaAdapter(endpoint="http://localhost:11434", model="llama2")
orchestrator = WorkflowOrchestrator(output_base="./my_output")
parser = WorkflowParser()

# Parse user input
user_input = "Download 6lu7 and ATP, prepare and dock"
parsed = parse_workflow_with_llm(llm_client, user_input)

# Validate workflow
workflow = parser.validate_workflow(parsed)

# Check for missing parameters
if parser.has_missing_parameters():
    print(parser.format_missing_prompt())
    # Handle missing parameters...

# Execute workflow (job_name is optional and purely a display label)
results = orchestrator.execute_workflow(workflow, job_id="my_job", job_name="My batch run")

print(f"Status: {results['status']}")
print(f"Output: {orchestrator.output_base / results['job_id']}")
```

---

## Performance Tips

### For Faster Inference

1. **Use GPU:** Ensure NVIDIA Docker support is enabled
2. **Choose smaller model:** Phi-3 is much faster than Llama 2
3. **Reduce temperature:** Lower temperature = faster but less creative
4. **Keep model loaded:** Ollama keeps models in memory after first use

### For Better Quality

1. **Use Llama 2 or Mistral:** Better understanding of complex requests
2. **Be explicit:** Include all parameters in your request
3. **Use proper terminology:** "docking box center" vs "center point"

---

## Examples Gallery

### Example 1: Quick Screening

```bash
python nlp_orchestrator.py "Download ATP and 6lu7, prepare them, dock with center 10,20,30 and box 20,20,20, use exhaustiveness 4"
```

**Output:** Fast docking (~2-5 min per ligand)

### Example 2: Standard Workflow

```bash
python nlp_orchestrator.py "Download ligands ATP, HEM, NAD and protein 6lu7, prepare at pH 7.4, dock with center 10,20,30 and box size 25,30,35 using 8 CPUs"
```

**Output:** Standard quality docking (~10-20 min per ligand)

### Example 3: High-Quality Publication

```bash
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare with optimization, dock with center 10,20,30 and box 25,30,35, exhaustiveness 32, population 2000, then visualize with ray tracing"
```

**Output:** Publication-quality results (~30-60 min per ligand)

---

## Advanced Configuration

### Custom Ollama Settings

Edit `docker-compose.yml`:

```yaml
ollama:
  environment:
    - OLLAMA_HOST=0.0.0.0
    - OLLAMA_KEEP_ALIVE=24h  # Keep model loaded
    - OLLAMA_NUM_PARALLEL=2  # Parallel requests
    - OLLAMA_MAX_LOADED_MODELS=2
```

### Custom Output Directory

```bash
python nlp_orchestrator.py --output-dir /path/to/results "..."
```

### Logging Configuration

```bash
# Debug mode
python nlp_orchestrator.py --debug "..."

# Or set environment variable
export LOG_LEVEL=DEBUG
python nlp_orchestrator.py "..."
```

---

## Testing

### Run Tests

```bash
# All tests
python -m pytest tests/test_llm_orchestrator.py -v

# Adapter and provider wiring tests
python -m pytest tests/test_llm_adapters.py -v

# Specific test
python -m pytest tests/test_llm_orchestrator.py::TestLLMArchitecture::test_parse_workflow_with_llm -v

# With coverage
python -m pytest tests/test_llm_orchestrator.py --cov=services.llm_orchestrator
```

### Manual Testing

```bash
# Check health
python nlp_orchestrator.py --check-health

# List models
python nlp_orchestrator.py --list-models

# Test parsing (without execution)
python nlp_orchestrator.py --debug "Download 6lu7"  # Stop before execution
```

---

## Roadmap

### Already Shipped (moved out of "planned")

- [x] Local ligand/protein files, not just RCSB (`--protein-file`, `--ligand-files`, `--ligand-folder` — see [Local File Input](#local-file-input))
- [x] Web UI (the Streamlit GUI, `ui/app.py` — see [GUI_GUIDE.md](GUI_GUIDE.md))
- [x] Result comparison (`compare_jobs()` / `nlp_orchestrator.py compare` — see [STATE_PERSISTENCE_STRATEGY.md](STATE_PERSISTENCE_STRATEGY.md#job-comparison))
- [x] More LLM backends (OpenAI, Anthropic, any OpenAI-compatible server — see [Supported LLM Providers](#supported-llm-providers))
- [x] Workflow history (revision history / `undo` in interactive mode — see [Revision history and undo](#revision-history-and-undo))

### Planned Features

- [ ] Batch workflow execution across multiple independent jobs (as opposed to the existing single-job [batch docking from a folder](BATCH_DOCKING.md))
- [ ] Workflow templates
- [ ] Multi-intent command sequencing in one input line (`dev_plan.md` Phase 10 — see [Current command granularity](#current-command-granularity))
- [ ] Parameter suggestions based on protein structure

---

## FAQ

**Q: Do I need an internet connection?**  
A: Only for initial model download and RCSB structure downloads. LLM runs locally.

**Q: Can I use a different LLM?**  
A: Yes, in two ways: switch Ollama models via `--llm-model` without rebuild, or switch providers entirely — Ollama, OpenAI, Anthropic, and any OpenAI-compatible server (LM Studio, vLLM, Groq, ...) all ship as built-in adapters. See [Supported LLM Providers](#supported-llm-providers).

**Q: How accurate is the parameter parsing?**  
A: Very good for clear, explicit commands. If ambiguous, the system will prompt for clarification.

**Q: Can I run this without a GPU?**  
A: Yes! CPU-only works but is slower. Remove the GPU config from docker-compose.yml.

**Q: What if the LLM makes a mistake?**  
A: The workflow parser validates all parameters. Invalid values are caught before execution.

**Q: Can I modify the workflow after parsing?**  
A: Yes, in interactive mode you can provide missing parameters. For programmatic use, modify the workflow dict before execution.

---

## Supported LLM Providers

The platform works with any of the providers below — no code changes required. Set the
provider in your `.env` file (copy `.env.example`) or via CLI flags.

### Ollama (default — local, private, free)

Runs entirely on your machine. Supports DeepSeek, LLaMA, Mistral, Qwen, Phi, and hundreds
of other models — just change the model name.

```bash
LLM_PROVIDER=ollama
LLM_ENDPOINT=http://localhost:11434
LLM_MODEL=qwen2.5-coder:7b-instruct-q4_K_M
```

CLI: `--llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model <model>`

### OpenAI

Cloud provider. Requires an API key. `gpt-4o-mini` is recommended for cost efficiency.

```bash
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

CLI: `--llm-provider openai --llm-api-key sk-... --llm-model gpt-4o-mini`

### Anthropic

Cloud provider. Requires an API key.

```bash
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
```

CLI: `--llm-provider anthropic --llm-api-key sk-ant-... --llm-model claude-haiku-4-5-20251001`

### OpenAI-compatible (LM Studio, vLLM, Groq, Together AI, etc.)

Any server exposing `POST /chat/completions` and `GET /models` in the OpenAI format.

```bash
# LM Studio (local):
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio        # placeholder — required to be non-empty by some servers
LLM_MODEL=local-model

# Groq cloud:
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...
LLM_MODEL=llama3-8b-8192
```

CLI: `--llm-provider openai_compatible --llm-base-url <url> --llm-api-key <key> --llm-model <model>`

### Privacy summary

| Provider | Data stays local? | Cost |
|---|---|---|
| Ollama | Yes | Free |
| OpenAI-compatible (local) | Yes | Free |
| OpenAI | No | Per token |
| Anthropic | No | Per token |
| Groq / Together AI | No | Free tier / per token |

---

## References

- **Ollama:** https://ollama.com/
- **Model Context Protocol (MCP):** Used for service communication
- **Docking Parameters Guide:** See `docs/DOCKING_PARAMETERS.md`
