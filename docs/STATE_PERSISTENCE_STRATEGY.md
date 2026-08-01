# Hybrid Workflow Persistence

## Overview
The workflow uses a hybrid persistence model that combines:
- latest files for simple runtime reloads
- immutable revisioned checkpoints for audit history and in-session undo (`undo <rev>` in the interactive CLI)

This is implemented in `services/llm_orchestrator/workflow_state_manager.py` and integrated into parser, orchestrator, and CLI flow.

**CLI only.** `ui/orchestrator_bridge.py` never passes a `state_manager=` to
`execute_workflow()`, so the Streamlit web UI has none of this: no
checkpointing, no `history`, no `undo`, no partial rerun. The UI only runs
one-shot workflows. Everything below describes the interactive CLI session
(`nlp_orchestrator.py --interactive`).

**Not covered by this:** resuming a killed/crashed process. Undo/restore
operates on `session.last_results`, which only exists in the memory of a
live interactive session -- if the process is killed, there is currently no
code path that reloads checkpoints to resume where it left off. The atomic
writes below guarantee files on disk are never left half-written, but that's
a different guarantee from being able to pick a workflow back up after a
crash.

## Files Written Per Job
For each job directory (`orchestrator_output/job_<id>/`), the system writes:

- `workflow_parameters.json`
  - latest persisted parameter set
  - includes `revision`, `timestamp`, `source`, `parameters`
- `workflow_state.json`
  - latest persisted runtime state
  - includes `revision`, `timestamp`, `step`, `state`
- `workflow_manifest.json`
  - monotonic revision counter and event log
  - includes `current_revision`, `events[]`
- `checkpoints/rev_XXXXXX_parameters.json`
  - immutable snapshot for each parameter update revision
- `checkpoints/rev_XXXXXX_state.json`
  - immutable snapshot for each runtime state checkpoint revision

## Atomic Writes
All JSON writes are atomic:
1. write payload to `*.tmp`
2. replace target via `os.replace(...)`

This prevents partial/corrupt writes if a process crashes mid-save.

Implementation detail (Phase 3):
- Atomic write/read helpers are centralized in `common/file_io.py`
- `WorkflowStateManager` and `common.utils.save_metadata`/`load_metadata` now use these shared helpers
- This removes duplicated JSON persistence logic and keeps behavior consistent

## Revision Model
Revisions are global per job and strictly increasing.
Each save operation increments revision and appends an event to manifest:
- `parameters_update` with details `{ "source": ... }`
- `state_update` with details `{ "step": ... }`

This means parameter and state saves share one revision timeline.

## Where It Is Integrated

### 1) Parameter persistence on update
`WorkflowParser` now supports a state manager and persists by default on updates.

- constructor accepts optional `state_manager`
- `set_state_manager(...)` to attach/detach persistence for a run
- `update_parameters(..., source=..., persist=True)` saves checkpoint when manager exists

File: `services/llm_orchestrator/workflow_parser.py`

### 2) Disk reload before each service step
`WorkflowOrchestrator.execute_workflow(..., state_manager=...)` now:
- reloads latest parameters from disk before each step
- reloads latest runtime state from disk before each step
- saves runtime state checkpoint after each successful step

Path normalization used by orchestrator is centralized in `common/path_utils.py`.

File: `services/llm_orchestrator/orchestrator.py`

This is the key zombie-parameter guard: disk is re-read before step execution.

### 3) CLI run wiring
`process_workflow(...)` now:
- creates one `WorkflowStateManager` per job
- persists initial parsed parameters (`source="initial_parse"`)
- attaches manager to parser during clarification
- reloads latest parameters before main execution
- passes manager into prep and execution flows
- detaches manager in `finally`

File: `nlp_orchestrator.py`

## Runtime Behavior Summary
1. Parse request
2. Validate + persist initial parameters
3. Clarification updates persist each parameter change
4. Before every step, reload latest parameters/state from disk
5. Execute step
6. Persist state checkpoint + revision snapshot
7. Continue until completion

## Why This Prevents Zombie Parameters
Without reload-before-step, stale in-memory values can survive longer than intended.
With this implementation:
- each step re-reads latest disk values
- latest file is source of truth for execution
- immutable checkpoint history preserves provenance

So stale memory is not trusted for step input.

## Test Coverage
Implemented tests in `tests/test_llm_orchestrator.py`:

- `test_update_parameters_persists_when_state_manager_attached`
  - verifies parser parameter updates persist through manager

- `test_execute_workflow_prefers_latest_disk_parameters`
  - verifies orchestrator uses disk-loaded parameters over stale workflow input
  - confirms reload-before-step behavior

- `test_saves_revisioned_checkpoints_and_latest_files`
  - verifies revision increments (`1,2,3`)
  - verifies immutable checkpoint files are created
  - verifies latest parameter/state loaders return expected payloads

Execution result in container dev environment:
- `docker compose --profile dev run --rm script-runner pytest tests/test_llm_orchestrator.py -q`
- all tests passed

## Notes and Limits
Current implementation provides:
- atomic writes
- monotonic revisions
- latest + immutable history
- reload-before-step

Not yet implemented:
- explicit file locking across competing writers
- optimistic concurrency checks against expected revision before write
- resuming a killed/crashed process from its checkpoints (undo/restore only
  works within a live interactive session -- see Overview)

These can be added if you want multi-process write contention protection or
crash resume.

---

## Phase 4: Revision History and Undo/Restore

### New Methods on `WorkflowStateManager`

**`list_revisions() -> List[Dict]`**

Returns all recorded revision events from the manifest in chronological order.
Each entry includes:
- `revision` — integer, monotonically increasing
- `timestamp` — ISO-8601 UTC string
- `event_type` — one of `parameters_update`, `state_update`, `undo_restore`
- `details` — dict (e.g. `{"source": "interactive_update"}` or `{"restored_from_revision": 3}`)
- `has_parameters` — True when a `rev_XXXXXX_parameters.json` checkpoint exists
- `has_state` — True when a `rev_XXXXXX_state.json` checkpoint exists

**`load_checkpoint(revision: int) -> Dict`**

Reads both checkpoint files for `revision` (whichever exist) and returns:
- `revision`, `parameters` (dict or None), `runtime_state` (dict or None)

Raises `ValueError` when neither file exists for that revision.

**`restore_revision(revision: int) -> Dict`**

Promotes a prior checkpoint to the "latest" files:
1. Loads the checkpoint via `load_checkpoint`.
2. Overwrites `workflow_parameters.json` and/or `workflow_state.json` with the checkpoint values.
3. Writes new immutable checkpoint files under the new (incremented) revision number.
4. Records an `undo_restore` event in the manifest so the restore itself is auditable.

Returns the same dict as `load_checkpoint`.

### Interactive Commands

| Command | Behavior |
|---|---|
| `history` | Calls `list_revisions()` and prints a table of all events |
| `undo <revision>` | Calls `restore_revision(n)`, updates latest files, marks all params as changed |

After `undo`, `session.pending_changed_params` is populated with all parameter keys so `rerun` will execute all affected downstream steps.

### Undo/Restore Flow

```
user: undo 3
  → load_checkpoint(3)           # validate revision exists
  → restore_revision(3)          # overwrite latest files, record undo event
  → session.pending_changed_params |= {protein_id, ligand_ids, center, box_size}
user: rerun
  → compute_rerun_steps(pending_changed_params, original_services)
  → _execute_partial_rerun(...)
```

### Safety Properties

1. `restore_revision` never silently ignores a missing revision — it raises `ValueError`.
2. The restore itself gets a new revision, so the undo is auditable and can itself be undone.
3. Only fields present in the checkpoint file are restored. If only a parameters checkpoint exists at that revision, runtime state is not touched.
4. `list_revisions` reads the manifest (not the filesystem), so it is consistent with what was actually written.

### Test Coverage

`tests/test_workflow_state_manager.py`:
- `test_list_revisions_empty_initially`
- `test_list_revisions_is_monotonic`
- `test_list_revisions_event_types`
- `test_list_revisions_has_parameters_flag` / `has_state_flag`
- `test_load_checkpoint_parameters` / `_state` / `_both`
- `test_load_checkpoint_invalid_revision_raises`
- `test_restore_revision_updates_latest_parameters`
- `test_restore_revision_records_undo_event`
- `test_restore_revision_increments_current_revision`
- `test_restore_revision_also_restores_state_when_available`
- `test_restore_revision_invalid_revision_raises`
- `test_restore_revision_does_not_resurrect_stale_values`
- `test_available_revisions_returns_sorted`

---

## Post-Phase 4 Interactive Guardrails

### `last_status` reflects pending parameter changes

`InteractiveSession.state_snapshot()` returns `"parameters_changed"` as `last_status` whenever `pending_changed_params` is non-empty, overriding the last run status. Once a successful rerun clears `pending_changed_params`, the actual run status (e.g. `"completed"`) is shown again.

This means `show state` always reflects whether the current parameters match the last docked result.

### Save blocked when parameters are pending

`save results as <name>` is refused when `pending_changed_params` is non-empty:

```
❌ Cannot save: parameters changed since last docking (center, box_size).
   Run 'rerun' first, or 'undo' to revert parameters.
```

This prevents saving a bundle where the metadata parameters do not match the docking results on disk.

### Pre-flight parameter validation before rerun

Before showing the rerun plan or asking for confirmation, all required parameters for the planned steps are checked against `REQUIRED_PARAMETERS_BY_STEP`. If any are missing or empty:

```
❌ Cannot rerun — required parameters are missing:
   • center (required by 'dock')
   • box_size (required by 'dock')
   Update them with a command like 'set protein to 6lu7' or restore a complete revision.
```

This prevents a `KeyError` crash inside the orchestrator and gives the user a clear remediation path.

---

## Job Comparison

### What it reads

`compare_jobs()` (`services/llm_orchestrator/job_comparator.py`) reads each job's
`workflow_results.json` — the same file written at the end of `execute_workflow()` — and
extracts `results.dock.results[].{ligand_id, best_affinity}` for each job. No other
persistence file (parameters, state, checkpoints) is involved; comparison is purely a
read-only operation over completed jobs.

### CLI usage

```bash
python nlp_orchestrator.py compare orchestrator_output/job_abc orchestrator_output/job_xyz
```

This is handled as a special case at the very top of `main()`, before the LLM client or
orchestrator are constructed — comparing jobs needs no LLM and no MCP services, only
local files, so neither is initialized for this subcommand.

### How to interpret the table

Columns are jobs, rows are ligands, cells are best binding affinity in kcal/mol. A ligand
missing from a job's results shows `N/A`. Affinity is more favorable the more negative it
is, so `compare_jobs()`'s `best_per_ligand` picks the *minimum* (most negative) value per
ligand row across jobs.

If a job docks the same `ligand_id` more than once (e.g. "two WWO ligands"), each
occurrence after the first is disambiguated as `WWO #2`, `WWO #3`, etc. in dock-result
order, so no duplicate result is silently dropped. These disambiguated rows only align
across jobs that have the same number of occurrences of that ligand — a job with a single
`WWO` has no `WWO #2` row, so that cell reads `N/A`.

### Test coverage

`tests/test_job_comparator.py` covers `load_job_affinities()` and `compare_jobs()`
directly (missing files, empty dock results, missing-ligand alignment, best-per-ligand
selection). `tests/test_nlp_orchestrator.py` covers the `compare` subcommand's argument
handling and ASCII table formatting.
