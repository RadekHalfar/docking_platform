# LLM Workflow Orchestration

Natural-language interface for molecular docking workflows using modular LLM providers and MCP services.

---

## Overview

This system lets you control the full molecular docking pipeline with plain-language commands. A provider-configured LLM parses each request, extracts parameters, and orchestrates calls across MCP services.

### Key Features

- **Natural Language Input** - Describe workflows in plain English.
- **Modular LLM Architecture** - Provider interface + adapters (none configured by default; pick a cloud provider or a local Ollama).
- **Runtime Model Switching** - Change model with flags/env vars, no rebuild required.
- **MCP Integration** - Coordinates all services through a single workflow.
- **Smart Defaults** - Applies defaults when optional parameters are omitted.
- **Validation** - Checks required parameters and prompts for missing values.
- **Docker Deployment** - The MCP services and GUI start with `docker compose up -d`; a local LLM is one opt-in profile away.

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
│      LLM Provider Adapter (chosen provider)         │
│  - Provider interface (BaseLLMClient)               │
│  - Adapter implementations (Ollama, OpenAI, ...)    │
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

  ### Centralized Workflow Metadata

  Workflow structure is defined in one place: [services/llm_orchestrator/workflow_definition.py](../services/llm_orchestrator/workflow_definition.py).

  This shared module provides:
  1. Canonical step order
  2. Required parameters per step
  3. Default workflow parameters
  4. Parameter invalidation map for partial reruns
  5. MCP service endpoint defaults and env-var overrides

  `WorkflowParser`, `rerun_planner`, and `WorkflowOrchestrator` consume this metadata directly.

---

## Quick Start

### 1. Start All Services

```bash
docker compose up -d
```

### 2. Choose an LLM

No LLM provider is configured by default (`LLM_PROVIDER=none`), and every
natural-language command needs one. Without it the CLI prints how to set one
up and exits; `--check-health` reports the LLM as "not configured".

**A cloud provider** (no download; see [Supported LLM Providers](#supported-llm-providers)):

```bash
python nlp_orchestrator.py --llm-provider openai --llm-model gpt-4o-mini --llm-api-key sk-... "Download 6lu7 and ATP"
```

**A local Ollama** (opt-in container, ~4.8 GB image plus a model):

```bash
docker compose --profile local-llm up -d ollama

# Download a model (one-time; persists in the ollama-data volume)
docker exec -it mcp-llm ollama pull gemma3:4b            # ~3.3 GB, lighter
# docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M   # ~4.7 GB
# docker exec -it mcp-llm ollama pull phi3               # ~2 GB, fastest
```

The GUI's Settings can download these too, with a progress bar (see
[GUI_GUIDE.md](GUI_GUIDE.md)); the models land in the same volume, so the
CLI sees them.

To avoid repeating flags, set `LLM_PROVIDER`, `LLM_MODEL` (and
`LLM_API_KEY`) in `.env` (see `.env.example`). Docker Compose passes them to
`script-runner` and `ui`; a `python nlp_orchestrator.py` run on the host
doesn't read `.env`, so export them in your shell there.

### 3. Check Service Health

```bash
python nlp_orchestrator.py --check-health
```

### 4. Optional: Select Provider/Model at Runtime

```bash
# Ollama with a given model
python nlp_orchestrator.py --llm-provider ollama --llm-model mistral "Download 6lu7 and ATP"

# Explicit endpoint (e.g. an Ollama app on the host, or a GPU workstation)
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

Accepted protein extensions: `.pdb`, `.cif`, `.mmcif` (mmCIF is converted to PDB with
gemmi during protein preparation). Accepted ligand extensions: `.sdf`, `.mol`, `.mol2`,
`.pdbqt`. An invalid extension or a path that doesn't exist is reported as a
missing/invalid parameter the same way an unresolvable PDB ID would be. A multi-model
protein file (an NMR entry) is docked against its first model.

A single `.sdf` path given to `--ligand-files` may itself contain multiple molecules
(`$$$$`-delimited records) — all of them are docked, not just the first. See
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files) for naming and
partial-failure behavior.

These flags also work with `--interactive` mode and apply to every `new_workflow` request
issued in that session.

For docking one protein against every ligand file in a folder, see
[Batch Docking from a Folder](BATCH_DOCKING.md) (`--ligand-folder`).

### Interactive Mode

This section describes `nlp_orchestrator.py`'s interactive terminal
session. The web GUI (`ui_nice/`, logic in `ui/core/`) runs on the same
`InteractiveSession`/`WorkflowStateManager` and covers part of it with its
own controls: redocking after a parameter change, Load/resume, revision
history/undo, and named result bundles. GUI chat also accepts `show
history`, `undo N` and `save results/docking/visualization as <name>`,
resolved deterministically without an LLM call. See
[docs/GUI_GUIDE.md](GUI_GUIDE.md). See
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

#### Session behavior

1. First natural-language request starts the active session job.
2. After completion the CLI blocks and waits for a follow-up command.
3. Use `state` to inspect the active job (disk-backed parameters).
4. Use `new <request>` to explicitly start a fresh workflow in the same session.
5. Use `end`/`quit`/`exit` or "end session" to stop.

#### Multi-intent command sequencing

A single input line may chain more than one follow-up command with `and`,
`then`, or `and then`:

```
🧬 [job_abc123] > state and save results as hem_box_a
🧬 [job_abc123] > show history then undo 14
🧬 [job_abc123] > change ligand to HEM and rerun docking
```

Each recognized step is printed as a numbered plan, then executed in order
after one confirmation for the whole sequence:

```
Multi-step command detected (2 steps):
  1. change ligand to HEM
  2. rerun docking
Execute these 2 steps in order? [Y/n]: y

[Step 1/2] change ligand to HEM
...
[Step 2/2] rerun docking
...

Multi-step command summary: 2/2 steps completed.
```

Execution **stops at the first step that fails** — the effects of any
earlier, already-completed steps are kept (they were already persisted to
disk before the failing step ran). The summary explains where it stopped:

```
Multi-step command summary: 1/2 steps completed. Stopped after step 2 ("rerun docking") failed.
```

An `end_session` step anywhere in the chain (e.g.
`save results as x and end session`) ends the session immediately after
running, same as typing it alone.

**How splitting works:** a boundary word (`and`/`then`) is only treated as a
split point when the text right after it independently looks like a
complete command on its own (deterministic pattern match — the same one
used for single-command classification, see below). This is what lets
`"set center 5,6,7 and box 20,20,20"` — a single compound
`update_parameters` command whose second half (`"box 20,20,20"`) isn't a
command on its own — run exactly as it always has, as one command, while
`"change ligand to HEM and rerun docking"` correctly splits in two (its
second half, `"rerun docking"`, is a recognizable command by itself).

For a `then`-containing input the deterministic splitter can't parse (e.g.
free-form phrasing like `"please show me the state then save it as trial
one"`), one additional LLM call is tried as a second pass — it proposes a
plain-text split, each piece is re-validated with the normal classifier
before being trusted, and the result is discarded (falling back to the
single-command path) if that validation fails. This second pass is **not**
attempted for `and`-only input, since "and" appears too often in ordinary
single commands to justify an LLM call on every occurrence. If even the
`then` case can't be split, a short guidance note is printed and the whole
input is dispatched as one command, unchanged:

```
🧬 [job_abc123] > dock it then somehow fix it
Note: treating this as a single command — chain complete commands with 'and'/'then', e.g. 'state and save results as trial_a'.
```

Splitting only ever applies to follow-up commands in an already-active
session — a `new <request>` (or the very first command before any job
exists) is always treated as one atomic natural-language request, since its
own text may legitimately contain "and"/"then"/"dock" as ordinary workflow
vocabulary (e.g. `"new Download 6lu7 and ADP, prepare and dock"`).

#### Intent classification for follow-up commands

When a job is active, every command is classified into one of these intents before dispatch:

| Intent | Example inputs | Behavior |
|---|---|---|
| `show_state` | `state`, `show state` | Print disk-backed parameters and status |
| `show_history` | `history`, `show history` | Print revision history table |
| `end_session` | `end`, `end session`, `quit session` | Exit the session cleanly |
| `new_workflow` | `new <request>` | Start a fresh workflow |
| `update_parameters` | `change ligand to HEM`, `set center to 10,20,30` | Extract updates via LLM, persist to disk, invalidate affected services |
| `set_protein_geometry` | `set up box center as protein center`, `look across all protein` | Compute `center`/`box_size` from the loaded protein's own bounding box, persist to disk, invalidate affected services ([below](#set-protein-geometry)) |
| `set_docking_algorithm` | `use vina`, `switch to PSO`, `dock with vina` | Switch the docking search; the "dock with …" forms rerun too ([below](#set-docking-algorithm)) |
| `search_ligand` | `search adenosine`, `find ligand heme` | Search RCSB ligands by name and pick one, which becomes the session's ligand ([below](#search-a-ligand-by-name)) |
| `save_results` | `save results as hem_box_a` | Save named result bundle from current docking/visualization outputs |
| `undo_revision` | `undo 14` | Restore parameters/state to a prior revision |
| `rerun_steps` | `rerun`, `dock`, `rerun docking` | Re-execute invalidated steps with pre-flight validation |
| `help` | `help`, `?` | Print command reference |

Common keywords (`state`, `history`, `end session`, `save results as <name>`, `undo N`, `new <request>`, `search <name>`) are resolved deterministically without an LLM call.
Ambiguous free-text commands (e.g. "change ligand to HEM") are sent to the LLM for classification.

#### Update parameters

After classifying `update_parameters`, the system:
1. Calls the LLM to extract the concrete field → value mapping.
2. Merges the changes with the current persisted parameters.
3. Saves a new checkpoint revision to disk.
4. Immediately removes invalidated services from the executed set (e.g. changing `center` invalidates `dock` and `visualize`).
5. Prints a confirmation with a hint to `rerun`.

#### Set protein geometry

`set_protein_geometry` is classified deterministically, like the other
common keywords — never sent to the LLM, since the LLM has no access to
the protein's atom coordinates and this codebase never lets it guess
`center`/`box_size` (`workflow_llm_parser.py::detect_protein_geometry_command()`,
checked in `_deterministic_intent()` just before the generic
`update_parameters` prefix match, so "set up box center as protein
center" isn't swallowed by it). After classifying, the system:
1. Reads the current session's last `prepare_protein` result
   (`InteractiveSession.last_results`) for the prepared receptor PDB path,
   translating it from the MCP service's own container mount to this
   process's local one (`common.path_utils.from_container_path`).
2. If no prepared protein exists yet, prints a message asking the user to
   run protein preparation first — no error, no guess.
3. Computes `center` (bounding-box midpoint) or `box_size` (bounding-box
   extent, +10% margin) from the file's `ATOM` records
   (`common.pdb_geometry.compute_protein_center_and_box`).
4. Persists the result exactly like `update_parameters` does (same
   `_persist_parameter_updates()` helper both intents share), invalidating
   `dock`/`visualize` and printing a `rerun` hint.

The GUI's chat (`ui/core/chat.py`) uses the same `detect_protein_geometry_command()`
function ahead of its own chat-parsing call, so both commands work
identically from the browser — see
[GUI_GUIDE.md](GUI_GUIDE.md#auto-filling-the-docking-box-from-the-protein).

#### Search a ligand by name

RCSB downloads a ligand by its component ID (`ATP`, `HEM`, `NAD`). When you
only know a name, the CLI searches RCSB's chemical component dictionary
(the `search_ligands` tool, [API.md](API.md#tool-search_ligands)) and lets
you pick the ID. It does this in two places.

**A name in a new request.** In the first request of a session, or a
`new <request>`, a ligand that doesn't look like a component ID (more than
four characters, or not only letters and digits) is searched before
anything runs, and you must choose:

```
🧬 > Download 6lu7 and adenosine, prepare and dock with center 10,20,30 and box 25,25,25

Detected ligand name 'adenosine' in workflow input. You must choose a specific RCSB ligand before execution.

Found 10 ligand candidate(s) for 'adenosine':
  1. A1II9 - A1II9 score=1.000
  2. ADN - ADN score=0.959
  3. AMP - AMP score=0.917
  ...
 10. 5FA - 5FA score=0.879
Choose ligand by index or component ID (blank to cancel workflow): 2
```

At most 10 candidates are listed, best first; RCSB returns only the IDs, so
each one's "name" is its ID again. Answer with the number or a component ID
typed directly. A blank answer, or
a name with no match, cancels the workflow. The same name used twice is
asked once. IDs in the same request (`ATP`) are used as they are. Newer
five-character IDs (such as `A1II9`) count as names here, so they go
through the list too; type the ID at the prompt to keep it.

**A follow-up `search`.** In an active session, `search <name>`,
`find <name>` or `lookup <name>` (optionally `search for ligand named …`)
runs the same search and list. With no name it asks for one. The chosen ID
**replaces all of the session's ligands**: it is saved as a new parameters
revision (source `search_ligand`, so `history` and `undo` see it), `dock` and
`visualize` are marked for rerun, and `rerun` then downloads and docks it.
A blank answer cancels without changing anything. A follow-up
`change ligand to <name>` is *not* searched, so use `search` instead (see
below for what happens to a name used as an ID).

**What a search finds.** It matches `chem_comp.name`, which is usually the
systematic name: "adenosine" finds ADN and AMP, but "aspirin" finds nothing
(its entry is named "2-(ACETYLOXY)BENZOIC ACID"; its ID is `AIN`). With no
match the CLI prints "No ligands found for '…'". The `rcsb-downloader`
service keeps each query's results in memory (case-insensitive) until it
restarts.

**The GUI has no ligand search.** Its chat and form take component IDs only.
A name there is downloaded as if it were an ID; that download fails, the
ligand is left out of the run, and if it was the only one the run fails with
"No ligand files available for preparation". Search in the CLI, or on
[rcsb.org](https://www.rcsb.org/), then enter the ID.

#### Set docking algorithm

`set_docking_algorithm` is also classified deterministically
(`workflow_llm_parser.py::detect_docking_algorithm_command()`). It is checked in
`_deterministic_intent()` before the `rerun|run|dock|execute` prefix, because
otherwise "dock with vina" would rerun with the old algorithm. It is
anchored over the whole command, so a longer sentence that only mentions an
algorithm ("dock 6lu7 with ATP", "dock with vina and visualize") doesn't
match. Recognised forms (case-insensitive, with an optional trailing
"algorithm"/"search"/"mode"):

| Command | Effect |
|---|---|
| `use vina`, `switch to PSO`, `change to moldina` | switch only |
| `set docking_algorithm vina`, `set the docking algorithm to monte carlo`, `algorithm: vina` | switch only |
| `dock with vina`, `redock using moldina`, `rerun with pso` | switch, then rerun |

Algorithm names: `vina`, `autodock vina`, `monte carlo`, `vina (monte carlo)`
→ `vina`; `moldina`, `pso`, `moldina (pso)` → `moldina`
(`common/docking_algorithm.py`). An unknown name ("use gnina") isn't
matched. It reaches the LLM `update_parameters` extraction, and
`_handle_update_parameters()` rejects it with "Docking algorithm 'gnina' is not
recognised…" instead of persisting it.

In the CLI, a switch persists a checkpoint (unless the session already uses
that algorithm) and invalidates `dock`/`visualize`. "dock with …" then goes
straight to the rerun plan (dock and visualize only, `[Y/n]`), also when the
session already uses that algorithm (a plain redock). If the LLM intent
classifier, rather than the detector, returns this intent, its name is
normalised the same way and an unknown one is rejected. The GUI
chat runs the same detector before its LLM parse. "use …" only switches;
"dock with …" switches and then does what **Run Docking** does.

#### GUI run command

The GUI chat also recognises a bare run command, `run`, `start docking`,
`redock it again`, `go ahead`, with
`workflow_llm_parser.py::detect_run_command()`. It is anchored over the
whole message, and only filler words (`it`, `the`, `docking`, `job`,
`again`, `now`, …) may follow the verb, so "dock ATP into 6lu7" or
"run 6lu7" still go to the LLM parse. It does what **Run Docking** does
(`PageController.run_docking`): it starts a new session or stages the
redock plan for the Confirm redock dialog, and any refusal is the chat's
reply. The CLI keeps its own broader `rerun|run|dock|execute` prefix in
`_deterministic_intent()`.

A GUI chat message changes only the form fields it names. The parser fills
every default (`docking_algorithm`, `cpu`, `exhaustiveness`, `ph`, …), and
a follow-up carries the previous turn's values, but neither reaches the
form, so the user's own choices survive the message. The first-parse prompt doesn't show `ph`/`exhaustiveness`/`cpu`/
`population` at their defaults in its examples, so the model doesn't echo
them as if the user had named them. An algorithm the LLM names but nobody
recognises is reported once ("…is not recognised, so Moldina (PSO) stays
selected") and is not carried into later follow-ups.

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
      1  2026-04-24T10:00:00.000...  parameters_update       source=initial_parse [params]
      2  2026-04-24T10:05:00.000...  state_update            step=dock [state]
      3  2026-04-24T10:10:00.000...  parameters_update       source=interactive_update [params]

  Parameters are currently at revision 3.
  Use 'undo <revision>' to restore any prior revision.
```

The number is the revision of the current *parameters*, the same one the
GUI's `show history` reports. A `state_update` revision (e.g. 2 above)
doesn't move it.

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

If the bundle name already exists, the CLI asks `Overwrite? [y/N]`. Overwriting replaces the whole old bundle. Declining saves under `<name>_HHMMSS` instead (`_2`, `_3`, ... appended if that is taken too), and the success line prints that final name. The GUI offers the same two choices as buttons (see [docs/GUI_GUIDE.md](GUI_GUIDE.md#saving-results-bundles)).

Valid bundle names: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` (max 64 characters) — no path separators, no `..`, no reserved names.

### Options

```bash
python nlp_orchestrator.py --help

Options:
  --interactive, -i         Run in interactive mode
  --llm-provider NAME       LLM provider name (default: LLM_PROVIDER, else none)
  --llm-endpoint URL        LLM API endpoint URL (default: LLM_ENDPOINT, else http://localhost:11434)
  --llm-model MODEL         LLM model name (default: LLM_MODEL)
  --llm-api-key KEY         Optional API key for providers that require auth
  --llm-base-url URL        Base URL for openai_compatible servers (e.g. http://localhost:1234/v1 for LM Studio)
  --output-dir DIR          Output directory (default: ./orchestrator_output)
  --protein-file PATH       Local protein .pdb/.cif/.mmcif file instead of downloading from RCSB
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
- "Dock all ligands in the folder into 6lu7 at pH 5.0" (with `--ligand-folder`; see [BATCH_DOCKING.md](BATCH_DOCKING.md))

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
- `docking_algorithm` - `moldina` (PSO, default) or `vina`
- `exhaustiveness` - Search thoroughness, Vina mode only (default: 8)
- `cpu` - CPUs per ligand, Vina mode only (default: 1)
- `population` - PSO population size, PSO mode only (default: 500)
- `num_modes` - Poses per ligand (default: 15)
- `energy_range` - Max energy difference from the best pose, kcal/mol (default: 100.0)
- `parallel` / `max_workers` - Dock several ligands at once (default: off / 1)

**Visualization:**
- `formats` - Which pose formats to generate for the GUI's 3Dmol.js viewer/downloads: any of `sdf`, `pdb`, `mol2` (default: all three; PDBQT is always available natively)

---

## Hybrid Clarification Parser

When required fields are missing or invalid, clarification runs in one of two modes:

- `hybrid` (default): LLM + deterministic safety checks
- `legacy`: deterministic prompts only

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
- Parses `center` and `box_size` from natural-language answers with the LLM.
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
└── job_20261029_070450_7a1a17/
    ├── downloads/
    │   ├── 6LU7.pdb
    │   ├── ATP.sdf
    │   ├── HEM.sdf
    │   └── 6lu7_download_meta.json
    ├── prepared/
    │   ├── 6LU7.pdbqt, 6LU7_prepared.pdb
    │   ├── ATP.pdbqt, ATP_prepared.pdb
    │   ├── HEM.pdbqt, HEM_prepared.pdb
    │   └── preparation_meta.json
    ├── docking_results/
    │   ├── ATP_out.pdbqt
    │   ├── HEM_out.pdbqt
    │   ├── ATP_moldina_output.log, HEM_moldina_output.log
    │   ├── center.txt
    │   ├── parameters.txt
    │   └── docking_progress.json
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
    ├── checkpoints/            (rev_XXXXXX_parameters.json / _state.json)
    ├── workflow_parameters.json, workflow_state.json, workflow_manifest.json
    ├── workflow_results.json
    └── job.log
```

The session files and `checkpoints/` back `state`, `history` and `undo`; see
[STATE_PERSISTENCE_STRATEGY.md](STATE_PERSISTENCE_STRATEGY.md).

**workflow_results.json** contains:
```json
{
  "job_id": "job_20261029_070450_7a1a17",
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
supplied. The GUI sets it from the **Session name** field at submission time
(or via the Sessions tab's **Rename** button afterward — see
[docs/GUI_GUIDE.md](GUI_GUIDE.md#naming-a-session)); the CLI (`nlp_orchestrator.py`)
does not currently set it, so CLI-run jobs always persist `"job_name": ""`
unless `execute_workflow(..., job_name=...)` is called directly.

`status` is one of `"completed"`, `"failed"`, or `"cancelled"` —
the last one written when a caller passes `execute_workflow(...,
cancel_event=...)` and that event gets set (e.g. the GUI's **Stop** button,
[docs/GUI_GUIDE.md](GUI_GUIDE.md#stopping-a-running-session)) before the
next step starts; `errors` then includes `"Workflow cancelled by user
request"`. A cancelled job's `services_executed`/`results` reflect whatever
actually finished before cancellation, exactly as a partial run's would.

`execute_workflow()` **merges** into any
`workflow_results.json` already on disk for that `job_dir` instead of
overwriting it: `results` dicts are merged per-step (the new call's step
results win on a repeat), `services_executed` is recomputed from every step
that now has a result, and an existing non-empty `job_name` survives a call
that didn't supply one. This means a session that runs `get_input_files`,
then later reruns only `dock` after a parameter change, still has both
steps' results on disk — a fresh `job_dir` with nothing to merge with
simply gets the new call's results.

**job.log** is a plain-text, append-only narration of a
session's whole timeline in one file — the one place a multi-redock session
reads end-to-end, since `workflow_results.json` only ever holds the current
merged snapshot and the revisioned checkpoints under `checkpoints/` are
structured data, not prose. Each line is
`<UTC ISO timestamp> [LEVEL] <message>`, written by both
`WorkflowOrchestrator.execute_workflow()` (workflow start, one line per step
completion with its duration, one line per MCP tool call, a full traceback
on failure, workflow finish + total elapsed time, and a pointer to the
per-ligand `docking_results/<ligand_id>_moldina_output.log` files rather
than duplicating their content) and `WorkflowStateManager` (a parameter-diff
line on every `save_parameters_checkpoint()` call, a one-liner on every
`save_execution_state_checkpoint()`/`restore_revision()` call) and
`save_results_bundle()` (one line per named-bundle save, success or
failure). See `common/job_log.py::append_job_log()`.

---

## Hardware Requirements

Only for a local LLM; a cloud provider needs none of this.

- **RAM:** 8–16 GB for `gemma3:4b`, 16 GB recommended for
  `qwen2.5-coder:7b-instruct-q4_K_M`
- **Storage:** about 8–9.5 GB (the Ollama image plus one model)
- **GPU:** optional. The Ollama container runs on the CPU (its GPU block in
  `docker-compose.yml` is commented out); the Ollama app on the host uses
  the GPU, or Apple Metal on a Mac.

---

## Model Selection

### Available Models

| Model | Size | Speed | Quality | Use Case |
|-------|------|-------|---------|----------|
| **gemma3:4b** | ~3.3GB | Fast | Good | Recommended, lighter; 8–16 GB RAM |
| **qwen2.5-coder:7b-instruct-q4_K_M** | ~4.7GB | Medium | High | Recommended, strong instruction following |
| **mistral** | ~4GB | Fast | High | Alternative |
| **phi3** | ~2GB | Very Fast | Good | Resource-constrained systems |

The GUI's Settings dialog offers the two recommended models; any other
Ollama model name works too.

### Download Models

```bash
# Qwen 2.5 Coder 7B Q4
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# Mistral (alternative)
docker exec -it mcp-llm ollama pull mistral

# Phi-3 (lightweight)
docker exec -it mcp-llm ollama pull phi3
```

### List Available Models

```bash
python nlp_orchestrator.py --llm-provider ollama --list-models

# OR
docker exec -it mcp-llm ollama list
```

### Use Different Model

```bash
python nlp_orchestrator.py --llm-provider ollama --llm-model mistral "Download 6lu7"

# Qwen 2.5 Coder 7B Q4
python nlp_orchestrator.py --llm-provider ollama --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7"
```

### Switch Models Without Rebuild

```bash
# Pull model once
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# Use it immediately (no docker image rebuild)
python nlp_orchestrator.py --llm-provider ollama --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP, prepare and dock"
```

---

## Troubleshooting

### Ollama Not Starting

Ollama is opt-in; a plain `docker compose up -d` doesn't start it.

**Start it:**
```bash
docker compose --profile local-llm up -d ollama
```

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
docker compose --profile local-llm restart ollama
```

### Model Not Found

**Download the model:**
```bash
docker exec -it mcp-llm ollama pull gemma3:4b
```

**Check available models:**
```bash
docker exec -it mcp-llm ollama list
```

### GPU Not Detected

The Ollama container's GPU block in `docker-compose.yml` is commented out;
uncomment it first. Then:

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
python nlp_orchestrator.py --llm-provider ollama --llm-model phi3 "..."
```

**Check GPU usage:**
```bash
nvidia-smi
```

### LLM Returns Invalid JSON

**Try different model:**
```bash
python nlp_orchestrator.py --llm-provider ollama --llm-model mistral "..."
```

**Enable debug mode:**
```bash
python nlp_orchestrator.py --llm-provider ollama --llm-model gemma3:4b --debug "..."
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
- **Run Ollama on the CPU** if you have a GPU (much slower; use the Ollama app on the host, or enable the container's GPU block)
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
llm_client = OllamaAdapter(endpoint="http://localhost:11434", model="gemma3:4b")
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

1. **Use GPU:** the Ollama app on the host, or the container with its GPU block enabled
2. **Choose smaller model:** `gemma3:4b` or `phi3` rather than a 7B model
3. **Reduce temperature:** Lower temperature = faster but less creative
4. **Keep model loaded:** Ollama keeps models in memory after first use

### For Better Quality

1. **Use a 7B model** (e.g. `qwen2.5-coder:7b-instruct-q4_K_M`) or a cloud provider: better understanding of complex requests
2. **Be explicit:** Include all parameters in your request
3. **Use proper terminology:** "docking box center" vs "center point"

---

## Examples Gallery

> The CLI examples below assume an LLM is configured, e.g. `LLM_PROVIDER=ollama`
> and `LLM_MODEL=gemma3:4b` in `.env`, or `--llm-provider ollama --llm-model
> gemma3:4b` on the command line. With the default (`none`), natural-language
> commands and `--list-models` print a setup message and exit 1.

### Example 1: Quick Screening

```bash
python nlp_orchestrator.py "Download ATP and 6lu7, prepare them, dock with center 10,20,30 and box 20,20,20"
```

**Output:** Default PSO search, a few seconds per ligand (2.5 s for ATP/6lu7, measured in [MOLDINA_DOCKING.md](MOLDINA_DOCKING.md))

### Example 2: Standard Workflow

```bash
python nlp_orchestrator.py "Download ligands ATP, HEM, NAD and protein 6lu7, prepare at pH 7.4, dock with center 10,20,30 and box size 25,30,35 with population 1000"
```

**Output:** Three ligands with a larger PSO population, still seconds each. To dock
them side by side, tick **Parallel ligand docking** in the GUI (see
[BATCH_DOCKING.md](BATCH_DOCKING.md))

### Example 3: High-Quality Publication

```bash
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare with optimization, dock with vina, center 10,20,30 and box 25,30,35, exhaustiveness 16 and 4 CPUs"
```

**Output:** Classic Vina search: about 1.8 min (106 s) per ligand at exhaustiveness 8
with 1 CPU, scaling with exhaustiveness and dropping with CPUs (measured in
[MOLDINA_DOCKING.md](MOLDINA_DOCKING.md))

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
# Debug mode (with an LLM configured, or pass --llm-provider/--llm-model)
python nlp_orchestrator.py --debug "..."
```

`--debug` is the only switch; no environment variable sets the log level.

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
python nlp_orchestrator.py --llm-provider ollama --list-models

# Test parsing (without execution): answer "n" at "Execute this workflow? [Y/n]"
python nlp_orchestrator.py --llm-provider ollama --llm-model gemma3:4b --debug "Download 6lu7"
```

---

## Roadmap

### Implemented

- [x] Local ligand/protein files, not just RCSB (`--protein-file`, `--ligand-files`, `--ligand-folder` — see [Local File Input](#local-file-input))
- [x] Web UI (NiceGUI, `ui_nice/` — see [GUI_GUIDE.md](GUI_GUIDE.md))
- [x] Result comparison (`compare_jobs()` / `nlp_orchestrator.py compare` — see [STATE_PERSISTENCE_STRATEGY.md](STATE_PERSISTENCE_STRATEGY.md#job-comparison))
- [x] More LLM backends (OpenAI, Anthropic, MetaCentrum, any OpenAI-compatible server — see [Supported LLM Providers](#supported-llm-providers))
- [x] Workflow history (revision history / `undo` in interactive mode — see [Revision history and undo](#revision-history-and-undo))
- [x] Multi-intent command sequencing in one input line (see [Multi-intent command sequencing](#multi-intent-command-sequencing))

### Planned Features

- [ ] Batch workflow execution across multiple independent jobs (as opposed to the existing single-job [batch docking from a folder](BATCH_DOCKING.md))
- [ ] Workflow templates
- [ ] Parameter suggestions based on protein structure

---

## FAQ

**Q: Do I need an internet connection?**  
A: For RCSB structure downloads, and for the LLM: a local Ollama needs it only to download a model, while a cloud provider (OpenAI, Anthropic, MetaCentrum) sends your commands over the network. No LLM is configured by default.

**Q: Can I use a different LLM?**  
A: Yes, in two ways: switch Ollama models via `--llm-model` without rebuild, or switch providers entirely — Ollama, OpenAI, Anthropic, MetaCentrum, and any OpenAI-compatible server (LM Studio, vLLM, Groq, ...) all ship as built-in adapters. See [Supported LLM Providers](#supported-llm-providers).

**Q: How accurate is the parameter parsing?**  
A: Very good for clear, explicit commands. If ambiguous, the system will prompt for clarification.

**Q: Can I run this without a GPU?**  
A: Yes. The Ollama container already runs on the CPU (its GPU block in docker-compose.yml is commented out); docking itself never uses a GPU.

**Q: What if the LLM makes a mistake?**  
A: The workflow parser validates all parameters. Invalid values are caught before execution.

**Q: Can I modify the workflow after parsing?**  
A: Yes, in interactive mode you can provide missing parameters. For programmatic use, modify the workflow dict before execution.

---

## Supported LLM Providers

The platform works with any of the providers below — no code changes required. Set the
provider in the GUI's Settings, in your `.env` file (copy `.env.example`; read by Docker
Compose for `ui` and `script-runner`), as exported variables for a run on the host, or via
CLI flags.

All providers share the same generic `LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY`/
`LLM_BASE_URL` environment variables rather than vendor-prefixed ones (e.g. there's no
separate `OPENAI_API_KEY` and `METACENTRUM_API_KEY`) — only one provider is active at a
time, selected via `LLM_PROVIDER`, so its variables are simply reused for whichever
provider you pick.

### None (default)

`LLM_PROVIDER=none`: no LLM. The GUI's forms, **Run Docking** and chat
commands (`run`, `use vina`, `history`, `save results as <name>`) work;
free text in the GUI chat gets a reply saying how to set an LLM up, and the
CLI's natural-language commands print the same advice and exit.

### Ollama (local, private, free; opt-in)

Runs entirely on your machine. Start the container with
`docker compose --profile local-llm up -d ollama`, or install the Ollama app
on the host (the GUI and the `script-runner` CLI reach it at
`http://host.docker.internal:11434`). Supports DeepSeek, LLaMA, Mistral, Qwen, Phi, and hundreds
of other models — just change the model name.

```bash
LLM_PROVIDER=ollama
# The bundled container; the compose default, so this line can be left out.
LLM_ENDPOINT=http://ollama:11434
LLM_MODEL=gemma3:4b
```

CLI: `--llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model <model>`
(`localhost` for a CLI run on the host; inside `script-runner` the default
`http://ollama:11434` applies). With a provider but no model, the CLI's
commands and `--interactive` say so and exit, and the GUI's **Apply**
refuses it.

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

### MetaCentrum (e-INFRA CZ hosted LLM gateway)

Academic/research cloud provider hosted by e-INFRA CZ. Requires an e-INFRA CZ bearer
token. Its gateway speaks the same OpenAI chat-completions API as the adapters above, so
`MetaCentrumAdapter` is a thin subclass of `OpenAICompatibleAdapter` with a built-in
default base URL and a longer default timeout.

```bash
LLM_PROVIDER=metacentrum
LLM_API_KEY=<your e-infra CZ bearer token>
LLM_MODEL=<verify via GET https://llm.ai.e-infra.cz/v1/models>
# LLM_BASE_URL=https://llm.ai.e-infra.cz/v1   # only needed to override the default
```

CLI: `--llm-provider metacentrum --llm-api-key <token> --llm-model <model>`

**Before using a model name from documentation or a colleague, verify it actually exists**
on the gateway: `curl -H "Authorization: Bearer <token>" https://llm.ai.e-infra.cz/v1/models`.
A model name that looks right in docs is not guaranteed to be deployed.

**Known caveat:** MetaCentrum's non-streaming endpoint has a hard, non-overridable
~30-minute server-side timeout. This project only sends short parameter-extraction
prompts, so a configurable client-side timeout (`MetaCentrumAdapter`'s `timeout` param,
default 300s) is used instead of implementing SSE streaming. If a future caller needs
long-form generation against MetaCentrum, either raise `timeout` further or add a
streaming code path — non-streaming calls that run past 30 minutes will otherwise be
killed by the server regardless of client-side settings.

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
| MetaCentrum (e-INFRA CZ) | No | Academic account — verify your allocation's terms; not necessarily per-token billed |
| Groq / Together AI | No | Free tier / per token |

---

## References

- **Ollama:** https://ollama.com/
- **Model Context Protocol (MCP):** Used for service communication
- **Docking Parameters Guide:** See `docs/DOCKING_PARAMETERS.md`
