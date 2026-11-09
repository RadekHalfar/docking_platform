# QUICK START

This quick start matches the current workflow behavior in this repository.

## 1. Build Images

Windows PowerShell:

```powershell
.\docker_build.ps1
```

Linux/Mac:

```bash
./docker_build.sh
```

## 2. Start Core Services

```powershell
docker compose up -d
```

The GUI is at http://localhost:3000 and docks from its forms without an LLM.

## 3. Verify Health

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health
```

## 4. Run a Complete Workflow

The CLI parses natural language, so it needs an LLM; none is configured by
default. Either set `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` in `.env`
(see `.env.example`), or start the opt-in local Ollama and pass it:

```powershell
docker compose --profile local-llm up -d ollama
docker exec -it mcp-llm ollama pull gemma3:4b   # or download it in the GUI's Settings
# then add: --llm-provider ollama --llm-model gemma3:4b
```

Example with explicit docking box:

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and two ATP, prepare and dock with center 10,20,30 and box size 25,30,35 and visualize"
```

Example without docking box (you will be prompted):

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py "Download 6lu7 and two ATP, prepare and dock and visualize"
```

## 5. Missing Docking Box: Hybrid Clarification Behavior

Default clarification mode is `hybrid`.

When `center` / `box_size` are missing, the CLI asks for them in natural
language and the LLM parses `center` / `box_size` from your free-text answer
(for example "center 10,20,30 and box size 25,30,35"). Type `state` at any
prompt to see what has been collected so far.

To pick the box visually instead, use the GUI's box picker (click an atom in
the 3D viewer); see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md).

Use deterministic-only behavior with legacy mode:

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --clarification-mode legacy "Download 6lu7 and two ATP, prepare and dock"
```

## 6. What `visualize` Does

If the workflow includes `visualize`, docked poses are converted to PDB/SDF/MOL2
via `convert_docked_poses` (on `ligand_preparer`). The GUI's in-browser
3Dmol.js viewer reads these files; see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md).

## 7. Output Location

All workflow outputs are saved under:

```text
orchestrator_output/job_<YYYYMMDD>_<HHMMSS>_<6 hex>/
```

Typical contents:

- `downloads/`
- `prepared/`
- `docking_results/`
- `visualization/` (if visualize is used)
- `saved_results/<name>/` (each **save results as** bundle, with its
  `docking_box.json`)
- `workflow_results.json`, plus the session files (`workflow_parameters.json`,
  `workflow_state.json`, `workflow_manifest.json`)
- `job.log` (the run's step-by-step log) and `checkpoints/` (parameter
  revisions for `history` / `undo`)

## 8. Run Tests

The hermetic suite (no network or live services; what CI runs):

```powershell
docker compose --profile dev run --rm script-runner python -m pytest -m "not integration"
```

The GUI tier, the real-docking test and the live-service tests each need
their own command; see the README's "Running Tests".

Targeted tests for this flow:

```powershell
docker compose --profile dev run --rm script-runner pytest tests/test_nlp_orchestrator.py tests/test_session_intents.py -q
```
