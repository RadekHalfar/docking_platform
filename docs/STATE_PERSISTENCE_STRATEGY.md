# Hybrid Workflow Persistence

## Overview
The workflow uses a hybrid persistence model that combines:
- latest files for simple runtime reloads
- immutable revisioned checkpoints for audit history and in-session undo (`undo <rev>` in the interactive CLI; **Restore revision** / `undo <rev>` in the GUI)

This is implemented in `services/llm_orchestrator/workflow_state_manager.py` and integrated into the parser, the orchestrator, the CLI and the GUI.

The CLI (`nlp_orchestrator.py --interactive`) and the GUI share the same
machinery:

- **Checkpointed parameters.** `ui/orchestrator_bridge.py::start_workflow()`
  accepts and threads through a `state_manager`, and the GUI keeps one
  `InteractiveSession` per browser client (`GuiSession.interactive_session`,
  `ui/core/state.py`) bound to the active job's `WorkflowStateManager`. The
  first **Run Docking** click creates it
  (`save_parameters_checkpoint(..., source="initial_parse")`).
- **Redock after a change.** Every later click while that job is active
  diffs the form against the last checkpoint
  (`interactive_session.py::compute_changed_params()`, a thin wrapper over
  the same `common/param_diff.py::changed_parameter_keys()` the job.log diff
  lines use). If anything changed, it checkpoints the new parameters,
  computes a minimal rerun plan with the same
  `rerun_planner.compute_rerun_steps()` the CLI uses, and executes it behind
  a **Confirm redock** button (see
  [docs/GUI_GUIDE.md](GUI_GUIDE.md#redocking-after-a-parameter-change)).
- **History and undo.** The GUI's **Revision history** panel and the
  `show history`/`undo N` chat commands use the same
  `list_revisions()`/`restore_revision()` as the CLI (see
  [docs/GUI_GUIDE.md](GUI_GUIDE.md#revision-history-and-undo) and
  [GUI behavior](#gui-behavior) below).
- **Result bundles.** The GUI's **Save results as…** panel and the
  `save results as <name>` chat command use the same
  `save_results_bundle()` (see
  [docs/GUI_GUIDE.md](GUI_GUIDE.md#saving-results-bundles) and
  [Save blocked when parameters are pending](#save-blocked-when-parameters-are-pending)).
- **Session resumption.** The GUI's **Load** button (both Session History
  and Failed Sessions) reconstructs a `WorkflowStateManager` against the
  selected job's existing `job_dir` and calls
  `session.activate(job_id, state_manager, last_results=...)`, so a session
  picked up after a browser refresh (which resets the GUI's in-memory state
  entirely) can be redocked immediately rather than only viewed read-only.
  Load also restores the session's parameters and the parameter widgets
  from that job's saved parameters (draft or checkpoint, see
  `draft_parameters.json` below). See
  [docs/GUI_GUIDE.md](GUI_GUIDE.md#session-history).
- **Stop.** The GUI's **Stop** button cancels cooperatively: it is checked
  between steps (see `WorkflowOrchestrator.execute_workflow`'s
  `cancel_event` parameter) and halts a running dock step within about a
  second via a `STOP_REQUESTED` flag file (see
  [docs/MOLDINA_DOCKING.md](MOLDINA_DOCKING.md#stopping-a-run)). The job is
  persisted with a `"cancelled"` workflow status alongside `"completed"` and
  `"failed"`; a cancelled job is not "completed", so it appears in the
  Failed Sessions list, not Session History (see
  [docs/GUI_GUIDE.md](GUI_GUIDE.md#failed-sessions)).

The rest of this document describes the interactive CLI session, except
where a section calls out GUI behavior explicitly.

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
- `draft_parameters.json`
  - in-progress parameter edits saved via the GUI's **Save Session**
    button, before any Run Docking/redock. Deliberately outside the
    revision/manifest system: written via
    `save_draft_parameters()`/read via `load_draft_parameters()`/removed
    via `clear_draft_parameters()`, with no `job.log` entry and no
    revision bump — so it can't spam the audit history, and can't
    interfere with the GUI redock's diff (`ui/core/run_control.py::plan_redock()`), which always
    compares against `load_latest_parameters()` (the real checkpoint)
    regardless of whether a draft exists. A GUI Load (`ui/core/sessions.py::resume_session()`) prefers
    this file over `workflow_parameters.json` when both are present; it's
    deleted the next time a real checkpoint is saved for that job, and by
    a **Save Session** that finds no change against the checkpoint (a
    stale draft would otherwise bring back an edit the form was set back
    from). Save Session diffs the form against the checkpoint with
    `compute_changed_params()`, so an unchanged form is reported as
    unchanged.
  - **Defaults are filled in one place.** `WorkflowStateManager` stores
    every parameters checkpoint and draft with all defaults filled in
    (`workflow_definition.with_default_parameters()`), and fills them in
    again on read, so a file that lacks a parameter (e.g. one written by a
    version without `docking_algorithm`) reads as the default it ran with.
    `compute_changed_params()` fills both sides the same way. A key missing
    on one side is therefore never a change: no bogus redock plan, no
    `None -> 'moldina'` line in `job.log`.
- `checkpoints/rev_XXXXXX_parameters.json`
  - immutable snapshot for each parameter update revision
- `checkpoints/rev_XXXXXX_state.json`
  - immutable snapshot for each runtime state checkpoint revision
- `job.log`
  - append-only, human-readable narration of the whole session timeline
    (parameter diffs, checkpoint/undo events, workflow start/step/finish,
    MCP calls, full tracebacks on failure) -- see `common/job_log.py` and
    [docs/LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md). Written by
    `WorkflowStateManager` and `WorkflowOrchestrator.execute_workflow()`
    regardless of whether a `state_manager` is attached, so it exists for
    every job. Both the CLI and the GUI attach a `state_manager` to their
    jobs, so the checkpoint/revision files above are written for both.

## Atomic Writes and Locking
All JSON writes are atomic:
1. write payload to `*.tmp`
2. replace target via `os.replace(...)`

This prevents partial/corrupt writes if a process crashes mid-save.

- Atomic write/read helpers are centralized in `common/file_io.py`.
- `WorkflowStateManager` uses these shared helpers (`atomic_write_json`, `load_json_dict`, `FileLock`).
- `FileLock` wraps `WorkflowStateManager._next_revision()`'s
  read-modify-write of the manifest, serializing competing writers.
  Optimistic concurrency checks (compare the expected revision before
  writing) are unnecessary on top of this: once writers are serialized by
  the lock, there's no race left for a compare-and-swap check to guard
  against.

## Revision Model
Revisions are global per job and strictly increasing.
Each save operation increments revision and appends an event to manifest:
- `parameters_update` with details `{ "source": ... }`
- `state_update` with details `{ "step": ... }`
- `undo_restore` with details `{ "restored_from_revision": ... }`

This means parameter and state saves share one revision timeline.

## Where It Is Integrated

### 1) Parameter persistence on update
`WorkflowParser` supports a state manager and persists by default on updates.

- constructor accepts optional `state_manager`
- `set_state_manager(...)` to attach/detach persistence for a run
- `update_parameters(..., source=..., persist=True)` saves checkpoint when manager exists

File: `services/llm_orchestrator/workflow_parser.py`

### 2) Disk reload before each service step
`WorkflowOrchestrator.execute_workflow(..., state_manager=...)`:
- reloads latest parameters from disk before each step
- reloads latest runtime state from disk before each step
- saves runtime state checkpoint after each successful step

Path normalization used by orchestrator is centralized in `common/path_utils.py`.

File: `services/llm_orchestrator/orchestrator.py`

This is the key zombie-parameter guard: disk is re-read before step execution.

### 3) CLI run wiring
`process_workflow(...)`:
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
With this design:
- each step re-reads latest disk values
- latest file is source of truth for execution
- immutable checkpoint history preserves provenance

So stale memory is not trusted for step input.

---

## Revision History and Undo/Restore

### `WorkflowStateManager` methods

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

The revision number must follow `undo` directly (`undo 3`, `undo rev 3`);
otherwise the CLI prompts for it and the GUI asks.

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

### GUI behavior

The GUI's **Restore revision** button and `undo N` chat command share
`ui/core/sessions.py::undo_to_revision()`. It calls the same
`restore_revision()`, then:

- clears `draft_parameters.json`, which would otherwise win over the restore
  on the next Load;
- drops any staged redock plan;
- reloads the form via `load_session_params()`, which resets every `param_*`
  widget first, so a key the revision lacks can't survive;
- sets `pending_changed_params |= CORE_PARAMS`, as the CLI does.

It refuses while the tab's own bridge is running, because
`execute_workflow()` re-reads the latest parameters before every step. It
also refuses a revision with no parameters checkpoint (a runtime-state-only
revision), *before* restoring anything: restoring only `workflow_state.json`
would roll the runtime state back under results it no longer matches with
nothing marked to rerun, and the panel's dropdown never offers those
revisions either. The CLI's `undo` does restore them, reporting the runtime
state restored.

The GUI's redock (`ui/core/run_control.py::plan_redock()`) plans from
`changed | pending_changed_params`. After a checkpoint it adds the changed
keys to `pending`. It clears `pending` only when a run of the active job
**completes**. A stopped or failed run keeps it, which is stricter than the
CLI's `rerun`, where `pending` is cleared either way. `InteractiveSession.activate()` resets
`pending` (the GUI reuses one session object across Loads).

`pending_changed_params` lives in memory only, so a Load (Sessions tab) re-seeds it
from disk: when `WorkflowStateManager.parameters_ran()` is False (a restore
not yet redocked, a cancelled redock plan), `resume_session()` sets
`pending |= CORE_PARAMS`. The per-key detail is not on disk, so this is an
over-approximation (a full rerun), the same one undo makes. This keeps a
loaded session consistent: redock and save agree on whether the current
parameters have been docked.

The redock also plans the steps the last run never reached when it did not
complete (`status != "completed"`: stopped or failed part-way), and allows
them past `compute_rerun_steps()`'s `original_services` intersection, which
otherwise only allows executed steps. This lets a session stopped during its
first dock step dock again. On the orchestrator side, `execute_workflow()`'s
results merge doesn't count a requested-but-unfinished step as executed, and
drops that step's older (now stale) result unless this run recorded its
own — so a stopped redock doesn't report `dock` as executed next to the
previous run's `visualize`.

### Safety Properties

1. `restore_revision` never silently ignores a missing revision — it raises `ValueError`.
2. The restore itself gets a new revision, so the undo is auditable and can itself be undone.
3. Only fields present in the checkpoint file are restored. If only a parameters checkpoint exists at that revision, runtime state is not touched.
4. `list_revisions` reads the manifest (not the filesystem), so it is consistent with what was actually written.

---

## Interactive Guardrails

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

The CLI also checks `WorkflowStateManager.parameters_ran()`: its `pending`
is per-process, so a revision restored in the GUI or in an earlier CLI
process would otherwise be saved over results it never produced.

**GUI.** The **Save results as…** panel and the chat `save
results as <name>` share `ui/core/save_results.py`: `plan_save()` (or
`plan_chat_save()`) plans the save on the event loop, `write_bundle()` copies
the files off it, and `finish_save()` reports the result. Like the CLI, it reads
the bundle's revision from `get_current_revision()` and its parameters from
`load_latest_parameters()`. Its guard, `save_block_reason()`, refuses:

- with no active session;
- while the tab's own bridge is running;
- when `pending_changed_params` is non-empty;
- when `WorkflowStateManager.parameters_ran()` is False, i.e. no
  `state_update` event follows the latest parameters revision in the
  manifest;
- when the session's `workflow_results.json` status is not `completed`.

The last two checks are the on-disk form of the `pending` check, so they
still hold after a Load or a restart, when `pending` (in memory only, see
[GUI behavior](#gui-behavior)) has been reset. Examples: a restore that was
never redocked, a redock plan that was cancelled, or a stopped or failed
redock. Without them, a bundle saved then would record parameters that never
produced its results — e.g. results docked with revision 10's parameters,
followed by five undocked restores, would be labelled revision 20.

**Overwrite and fallback name (CLI + GUI).**
`save_results_bundle(..., overwrite=True)` replaces the whole old bundle, so
a narrower-scope overwrite doesn't keep stale artifacts. The new bundle is
written to a `.<name>.partial` directory next to it and swapped in only once
complete, so a failed overwrite keeps the old bundle. A filesystem error
while writing (`OSError`) gives `success: False`, removes the partial copy
and keeps the old bundle. `timestamped_bundle_name()` gives the
`<name>_HHMMSS` fallback. If that name is taken, it adds `_2`, `_3`, ...,
and it shortens long names to stay within 64 characters. The CLI prints the
final name.

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

## Limits

Resuming a killed or crashed *process* is not supported. This is distinct
from the Load-based session resumption above, which only ever acts on a job
whose `execute_workflow()` call already returned and wrote its files.
Undo/restore only promotes an existing checkpoint file to the "latest"
files; it never re-runs or reloads an interrupted call. If the process
running `execute_workflow()` is killed mid-call (rather than stopped via the
GUI's cooperative Stop button, which always finishes cleanly), no code path
reloads checkpoints to resume that call. The atomic writes guarantee files
on disk are never left half-written, which is a different guarantee from
being able to pick an in-flight workflow call back up after a hard crash.

---

## Job Comparison

### What it reads

`compare_jobs()` (`services/llm_orchestrator/job_comparator.py`) reads each job's
`workflow_results.json` — the same file written at the end of `execute_workflow()` — and
extracts `results.dock.results[].{ligand_id, best_affinity}` for each job. No other
persistence file (parameters, state, checkpoints) is involved; comparison is purely a
read-only operation over completed jobs.

Column headers use each job's top-level `job_name` field when present (the optional,
non-unique display label set from the GUI's **Session name** field or renamed later via the
Sessions tab's **Rename** button — see [docs/GUI_GUIDE.md](GUI_GUIDE.md#naming-a-session)), falling
back to the job directory basename (i.e. the job ID) when a job has no name. Since the
label isn't unique, a label shared by several of the compared jobs gets each one's job ID
appended (`trial_a (job_…)`), in the GUI and the CLI alike, so every column label is distinct.

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

---

## Test Coverage

`tests/test_llm_orchestrator.py`:
- `test_update_parameters_persists_when_state_manager_attached` — parser parameter updates persist through the manager
- `test_execute_workflow_prefers_latest_disk_parameters` — the orchestrator uses disk-loaded parameters over stale workflow input (reload-before-step)
- `test_saves_revisioned_checkpoints_and_latest_files` — revisions increment (`1,2,3`), immutable checkpoint files are created, and the latest parameter/state loaders return the expected payloads

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

`tests/test_job_comparator.py` covers `load_job_affinities()` and `compare_jobs()`
directly (missing files, empty dock results, missing-ligand alignment, best-per-ligand
selection). `tests/test_nlp_orchestrator.py` covers the `compare` subcommand's argument
handling and ASCII table formatting.

Run them in the dev container:

```bash
docker compose --profile dev run --rm script-runner pytest tests/test_llm_orchestrator.py tests/test_workflow_state_manager.py tests/test_job_comparator.py -q
```
