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

## 3. Verify Health

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --check-health
```

## 4. Run a Complete Workflow

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

When `center` / `box_size` are missing, CLI first asks in natural language how to provide docking box coordinates (manual vs interactive PyMOL), then:

1. If intent is clear and manual is chosen, `center` / `box_size` are parsed from your free-text answer by LLM.
2. If intent is clear and PyMOL is chosen, orchestrator tries interactive PyMOL box capture directly.
3. If intent is ambiguous, CLI falls back to deterministic menu:
	- Write center/box_size manually
	- Define box in PyMOL Interactive now

If PyMOL path is used, orchestrator:

1. Prepares structures
2. Loads them in interactive PyMOL
3. Waits for your selection name (default: `sele`)
4. Calls interactive API to compute box and save `docking_box.json`
5. Continues docking in the same job folder

Use deterministic-only behavior with legacy mode:

```powershell
docker compose --profile dev run --rm script-runner python nlp_orchestrator.py --clarification-mode legacy "Download 6lu7 and two ATP, prepare and dock"
```

## 6. Interactive PyMOL

Already running -- it started along with everything else in step 2 (`docker compose up -d`). No profile flag or extra command is needed.

Open noVNC desktop:

```text
http://localhost:6080/vnc.html
```

Interactive helper API:

```text
http://localhost:8090
```

## 7. What `visualize` Currently Does

If workflow includes `visualize`:

1. Docked poses are converted to PDB/SDF/MOL2 via `convert_docked_poses` (on
   `ligand_preparer`) — this is what the GUI's in-browser 3Dmol.js viewer
   reads; see [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md)
2. After workflow completion, CLI tries to load the same job into `pymol-interactive`
3. You can inspect it in noVNC when `pymol-interactive` is running

## 8. Output Location

All workflow outputs are saved under:

```text
orchestrator_output/job_<timestamp_ms>/
```

Typical contents:

- `downloads/`
- `prepared/`
- `docking_results/`
- `visualization/` (if visualize is used)
- `docking_box.json` (if box was defined through interactive PyMOL)
- `workflow_results.json`

## 9. Run Tests

```powershell
docker compose --profile dev run --rm script-runner python -m pytest tests/ --tb=short
```

Targeted tests for this flow:

```powershell
docker compose --profile dev run --rm script-runner pytest tests/test_nlp_orchestrator.py tests/test_pymol_interactive.py -q
```

## 10. Rebuild Only Interactive Service (When Needed)

```powershell
.\docker_build.ps1 -Services pymol-interactive -NoCache
docker compose up -d pymol-interactive
```
