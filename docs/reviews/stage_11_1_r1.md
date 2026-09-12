# Review Stage R1 — Stage 11.1 (session log foundation + results-merge fix)

**Scope:** commit `e599df3` (`feat(orchestrator): add job.log session narration + fix
results-json clobber`), per `dev_plan.md` Phase 11. Runs `/code-review` (correctness)
and `/simplify` (reuse/simplification/efficiency/altitude) per
`development_process_rules.md` §4, ahead of Stage 11.2 GUI work as this review stage
is blocking.

**Outcome:** 6 correctness findings, 8 quality findings (reuse/simplification/
efficiency/altitude). 9 fixed directly (commit follows this report), 2 deferred with a
written trigger, 3 accepted as documented risk. Full suite green after fixes: 676
passed / 14 skipped / 1 deselected, no regressions
(`docker compose --profile dev run --rm script-runner python -m pytest -m "not
integration"`).

---

## Correctness (`/code-review`)

### 1. `workflow_results.json` merge-on-write had no locking and a non-atomic write
**File:** `services/llm_orchestrator/orchestrator.py` (was ~line 636)
**Triage: fixed immediately.**
The new merge (read existing file, merge `results` dicts, write back) raced two
overlapping `execute_workflow()` calls against the same `job_dir` — the exact
"silently clobbers" scenario the Stage 11.1 fix exists to prevent, just moved one
layer up, and the write itself used a plain `open(..., 'w')` instead of the repo's
existing `atomic_write_json`. Fixed by wrapping the read-merge-write in a
`FileLock(job_dir / ".workflow_results.lock")` (mirroring the exact pattern
`WorkflowStateManager._next_revision()` already uses for the manifest) and switching
the write to `atomic_write_json`.

### 2. Merge has no staleness/dependency check for a partial-step call
**File:** `services/llm_orchestrator/orchestrator.py`, merge block
**Triage: deferred, with trigger.**
A call with `services=["get_input_files", "dock"]` after an upstream input changed
would leave a stale downstream `visualize` entry (built from the old inputs) merged
into the persisted snapshot unchanged. Real, but every caller today reaches
`execute_workflow()` via `rerun_planner.compute_rerun_steps()`, which always includes
every downstream step when an upstream one reruns — so this cannot occur in practice
yet. **Recommendation: leave as-is**, not worth generalizing against a caller that
doesn't exist. Trigger placed: code comment at the merge site
(`orchestrator.py`, "NOTE (R1, Stage 11.1 review...)") naming the symptom, and a
matching Future Work bullet in `dev_plan.md` — both say to revisit if a caller ever
invokes `execute_workflow()` with a services list not computed by
`compute_rerun_steps()`.

### 3. 4 `call_mcp_tool` tests wrote real files outside `tmp_path`
**File:** `tests/test_llm_orchestrator.py` (`test_call_mcp_tool`,
`test_call_mcp_tool_error`, `test_call_mcp_tool_raises_on_http_error`,
`test_call_mcp_tool_raises_on_connection_error`)
**Triage: fixed immediately.**
These predate Stage 11.1 and construct `WorkflowOrchestrator()` with the default
`output_base`; once `call_mcp_tool()` started calling `append_job_log()`, they began
writing real `./orchestrator_output/{test_job,err_test,conn_test}/job.log` files
(plus transient lock files) into the actual working directory on every test run,
non-hermetically and reused across runs/CI. Fixed by passing `output_base=str(tmp_path)`
to match the pattern already used by the Stage-11.1-added
`test_call_mcp_tool_logs_one_line_per_call`. Stray directories from prior runs
(`orchestrator_output/{test_job,err_test,conn_test}/`, gitignored, untracked) deleted.

### 4. `job.log` line order not guaranteed to match manifest revision order
**File:** `services/llm_orchestrator/workflow_state_manager.py::_next_revision()`
**Triage: fixed immediately.**
`append_job_log()` was called after the `with FileLock(self.manifest_lock_file):`
block released the lock, so two concurrent writers could have their manifest-revision
order (correctly serialized) not match their job.log line order (not serialized against
the same lock) — job.log, "the one place a session's whole timeline is readable
end-to-end," could show events out of order. Fixed by moving the `append_job_log()`
call inside the `with FileLock(...)` block, right after the manifest write, so the two
orderings can't diverge.

### 5. `nlp_orchestrator.py::_execute_partial_rerun()` duplicates the new on-disk merge in memory
**File:** `nlp_orchestrator.py` (~line 395)
**Triage: deferred, with trigger.**
`execute_workflow()` now merges/recomputes `services_executed` on disk; this CLI
method still independently reassembles `session.last_results` in memory from
`original_services` + `results.get("results", {})`, unrelated to the on-disk
computation. If the two ever disagree, the CLI's view of "which steps are done"
silently diverges from what's persisted. **Recommendation: leave as-is for now** —
reconciling it means changing the CLI's tested rerun path, which is out of scope for a
backend-only stage and risks a CLI regression for a divergence that hasn't been
observed. Trigger placed: code comment at `nlp_orchestrator.py`'s merge site naming
the symptom and pointing back to this report; matching Future Work bullet added.

### 6. `call_mcp_tool()` fell back to a shared `"unknown_job"` log directory
**File:** `services/llm_orchestrator/orchestrator.py::call_mcp_tool()`
**Triage: fixed immediately.**
If `current_job_id` were ever falsy (a call made outside `execute_workflow()`),
`append_job_log()` would silently `mkdir`/write into a shared `unknown_job/job.log`,
interleaving lines from unrelated calls. Fixed: `call_mcp_tool()` now skips logging
entirely (rather than writing to a shared fallback location) when `current_job_id` is
unset.

---

## Reuse / Simplification / Efficiency / Altitude (`/simplify`, 4 parallel angles)

### 7. Merge write should reuse `atomic_write_json`/`FileLock` — same fix as #1
**Triage: fixed immediately** (folded into #1's fix).

### 8. Duplicate "which parameter keys changed" logic
**Files:** `services/llm_orchestrator/workflow_state_manager.py::_format_parameters_diff`
vs. `nlp_orchestrator.py`'s existing `changed_keys` computation, and Stage 11.2's
planned `interactive_session.py::compute_changed_params()`
**Triage: fixed immediately (partial), rest accepted.**
`_format_parameters_diff`'s union-based key-diff is now extracted into
`common/param_diff.py::changed_parameter_keys(old, new)` — a genuinely reusable helper,
since Stage 11.2 (next stage) was about to add a third near-identical
`compute_changed_params()` in `interactive_session.py`; it should now import this
helper instead of re-deriving the same logic a third time. `nlp_orchestrator.py`'s own
`changed_keys` (line 151) is **left as-is**: it computes over `updates`-only (not the
old|new union `changed_parameter_keys` uses) for a different purpose
(`pending_changed_params`, which drives rerun-step selection), so unifying it would
change CLI rerun-selection behavior — out of scope for this review pass, accepted as a
documented, intentionally-different implementation.

### 9. Duplicate UTC-ISO timestamp formatting
**Files:** `common/job_log.py` vs. `WorkflowStateManager._timestamp()`
**Triage: accepted as a documented risk.** Two identical one-line
`datetime.now(timezone.utc).isoformat()` expressions. **Recommendation: leave as-is** —
trivial duplication, no behavioral risk, and `job_log.py` is deliberately
dependency-light (only imports `FileLock`); introducing a shared timestamp helper for
one line each isn't worth the extra import surface.

### 10. `step_start_times` dict + `_step_started`/`_step_elapsed` closures were unneeded indirection
**File:** `services/llm_orchestrator/orchestrator.py::execute_workflow()`
**Triage: fixed immediately.**
All 5 workflow steps run strictly sequentially in one function, so a dict keyed by
step name (with a silent `time.time()` fallback if a step's start was never recorded)
added indirection for what is really "elapsed since this block started." Replaced with
a single local `step_t0 = time.time()` set at the top of each step block.

### 11. 5 near-identical `append_job_log(...)` step-completion calls
**File:** `services/llm_orchestrator/orchestrator.py::execute_workflow()`
**Triage: fixed immediately** (same pass as #10, since both touch the same call
sites). Collapsed into one `_log_step_done(step, start_time, detail)` closure, called
once per step with just the step-specific detail string. Log line text is unchanged
byte-for-byte.

### 12. `append_job_log()` per-call `mkdir` + lock-file overhead
**File:** `common/job_log.py`
**Triage: accepted as a documented risk. Recommendation: leave as-is** — each call is
paired with an MCP HTTP round-trip or a checkpoint write, both far more expensive than
one `mkdir(exist_ok=True)` + advisory-lock cycle; not a measurable cost at current job
sizes, and avoiding it (e.g. caching an open file handle for a run's lifetime) trades a
simple, always-correct-on-crash append for a stateful handle that has to be managed
across the same concurrent-writer boundary this file's docstring exists to handle.

### 13. `workflow_results.json` merge reads and re-parses the whole file every call
**File:** `services/llm_orchestrator/orchestrator.py`, merge block
**Triage: accepted as a documented risk. Recommendation: leave as-is** — the read is
what makes the merge correct (the orchestrator is otherwise stateless per call, so
there's no in-memory prior-results to merge against without it); removing the
round-trip would mean keeping results in memory across calls in a process that isn't
designed to, a materially bigger change not justified by current payload sizes.

### 14. Two separate lock acquisitions per manifest event (manifest lock + job.log lock)
**File:** `services/llm_orchestrator/workflow_state_manager.py::_next_revision()`
**Triage: accepted as a documented risk** (ordering half already fixed as #4).
**Recommendation: leave the two-lock structure as-is** — collapsing them to one lock
would mean `job_log.py` losing its own independent lock file (used by other callers
too, e.g. `call_mcp_tool()`), a bigger design change for a per-event cost that isn't
measurable at current event rates.

### 15. Merge-on-write is a field-specific special case, not a generic mechanism
**File:** `services/llm_orchestrator/orchestrator.py`, merge block
**Triage: accepted as a documented risk. Recommendation: leave as-is unless a second
on-disk-JSON-merge caller appears** — generalizing into a reusable "locked
read-modify-write with a merge policy" utility is speculative with only one caller;
the correctness gap (no lock/atomicity) is already fixed as #1, and Stage 11.1's plan
entry specifies exactly these merge semantics (`results`/`services_executed`/
`job_name`), not an open-ended one.

### 16. `log_message` param on `_next_revision()` pushes a formatting concern into a structural method
**File:** `services/llm_orchestrator/workflow_state_manager.py`
**Triage: accepted as a documented risk. Recommendation: leave as-is** — real
architectural point (the method's job was atomic counter + manifest append, now also
owns default log-line formatting), but only one caller
(`save_parameters_checkpoint`) needs the override, and splitting formatting into a
separate event→prose translator is a non-trivial restructure not justified without a
second caller needing that separation.

---

## Future Work additions

Two bullets added to `dev_plan.md`'s Future Work section, cross-referencing this
report, matching findings #2 and #5 above.
