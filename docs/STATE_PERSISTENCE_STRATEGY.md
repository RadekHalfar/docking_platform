# Hybrid Workflow Persistence

## Overview
The workflow uses a hybrid persistence model that combines:
- latest files for simple runtime reloads
- immutable revisioned checkpoints for audit history and recovery

This is implemented in `services/llm_orchestrator/workflow_state_manager.py` and integrated into parser, orchestrator, and CLI flow.

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

These can be added if you want multi-process write contention protection.

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
| `undo <revision>` | Calls `restore_revision(n)`, updates latest files, marks params as changed |

After `undo`, `session.pending_changed_params` is populated so `rerun` will execute the correct downstream steps.

### Undo/Restore Flow

```
user: undo 3
  → load_checkpoint(3)           # validate revision exists
  → restore_revision(3)          # overwrite latest files, record undo event
  → session.pending_changed_params |= all param keys
user: rerun
  → compute_rerun_steps(changed_params, original_services)
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
