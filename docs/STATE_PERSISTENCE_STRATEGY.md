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
