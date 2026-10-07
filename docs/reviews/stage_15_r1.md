# Review Stage R1 — Phase 15 (Stages 15.1–15.3) — Tier-3 review + Cross-Service Compatibility Review

**Scope:** `b5602ad..480c9a1`: everything since Phase 14's Review Stage R1
fix commit, which is exactly Phase 15's three stage commits:
- `7d168d9`: Stage 15.1, the runner, MCP tool, orchestrator and definitions.
- `349c056`: Stage 15.2, chat and CLI.
- `480c9a1`: Stage 15.3, the GUI controls and the Vina benchmark.

That is about 2,100 changed lines in 41 files. No commit landed between
Phase 14's review and this range, so there is no review gap to close
(`development_process_rules.md` §4).

Stage 15.1 changed the `run_docking` MCP tool signature and its result
shape, so rules §5's Cross-Service Compatibility Review runs as part of this
review.

**Method** (`development_process_rules.md` §4, §5):
- `/code-review` (correctness, high effort) over the range.
- `/simplify` over the same range, run report-only, with four parallel angle agents: reuse, simplification, efficiency and altitude.
- A dedicated exception-handling pass.
- A re-check of the Vina benchmark tables and the `MEASURED_*` constants against the checked-in JSON.
- The compatibility checks: the MCP signature against every caller, the result shape against every consumer, the `common/` change against every image, and compose/env/security.
- A real CLI session against the live services.

Every candidate was checked against the real code before triage. Every new
regression test was run against `480c9a1`'s production code in a temporary
worktree (see Verification).

**Two rules restated, not just pointed at:**
- Exceptions are never used as a band-aid. A broad catch belongs only at a real I/O/protocol boundary, and it must turn the failure into an explicit failed state, never make it vanish.
- No finding is manufactured to look thorough. Candidates not worth acting on are reported as declined, with the reason, not dressed up. The efficiency angle found nothing worth changing, and it is reported that way.

**Owner decisions during the review:**
- **Phase 14 R1's deferred finding #2** (fill parameter defaults in one place) had fired in substance (Quality #1). Asked whether to do the root fix now or only mirror the GUI's patch in the CLI, the owner chose the **root fix**.
- **A pre-existing CLI bug** (2026-04-14) turned up in the real-data run (Correctness #9). Asked whether to fix it now or defer it, the owner chose **fix now**.
- **The fresh-stack restart was not done by me.** Stopping the running stack (`mcp-ui` was live) was refused by the session's permission classifier, and that call belongs to the owner. The commands are in "Compatibility review → Fresh stack". The real-data run went against the live services instead, which tests the mixed-version case.

**Outcome:**
- **Correctness:** 9 findings (8 from `/code-review`, 1 from the real-data run). All 9 are fixed.
- **Quality (`/simplify`, plus 2 that `/code-review` filed as correctness):** 25 after dedup.
  - 12 fixed (one of them closes Phase 14 R1's deferred #2).
  - 2 deferred with written triggers.
  - 11 declined with reasons.
- **Exception pass:** clean. One silent fallback is documented and its fix is assigned to Phase 16.4.
- **Benchmark re-check:** clean.
- **Compatibility review:**
  - Signatures and shapes are compatible in both mixed-version directions.
  - The base image must be rebuilt before `moldina-runner`.
  - The fresh-stack run is left to the owner.

Test results are in "Verification" at the end.

---

## Correctness (`/code-review` + real-data run)

### 1. The `run_docking` timeout was too short for Vina
**Files:** `services/llm_orchestrator/orchestrator.py`. Introduced by Stage 15.1.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** the HTTP timeout was `max(300, 120 s × ligands / workers)`, a budget sized for PSO (about 2–3 s per ligand). Vina's search is far slower and grows with exhaustiveness.
- One 10-worker Vina wave at exhaustiveness 8 took about 144 s in the 15.3 benchmark. So 50 ligands at 10 workers need about 720 s, against a 600 s timeout.
- Exhaustiveness 16 roughly doubles the run time.

When the timeout expired, the dock step failed while the runner kept docking.

**Fix:** the rule moved into a pure `docking_timeout_seconds(num_ligands, workers, algorithm, exhaustiveness)`.
- **PSO:** unchanged at 120 s per ligand.
- **Vina:** `max(120, 30 × exhaustiveness)` per ligand, which is 240 s at the default 8. That is about 1.7× the measured worst wave.
- **No credit for `cpu` > 1:** Vina is CPU-bound, so splitting the quota into fewer multi-CPU workers doesn't shorten a batch (the 15.3 splits finished within 9%). It also covers the server cutting workers below the request.

**Same class in the benchmark:** `benchmark_docking.py`'s default `--timeout-per-ligand` was a flat 180 s, which a sequential Vina row at exhaustiveness 16+ can exceed. Its default is now `timeout_per_ligand()`: 180 s for PSO, `max(180, 30 × exhaustiveness)` for Vina. An explicit flag still wins. This was found while listing what the review had left undone.

**Tests:** `TestDockingTimeout` (4 cases) and `TestTimeoutPerLigand` (3 cases). The real `execute_workflow` path must send at least 1.5 × the measured time for 50 Vina ligands; the old code sent 600 s. The PSO budget is unchanged, the budget scales with exhaustiveness, and the 300 s floor holds.

### 2. PSO's workers were divided by `cpu`, which PSO ignores
**Files:** `services/moldina_runner/__init__.py`. Introduced by Stage 15.1. Merged with altitude finding #2.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** the server clamped workers to `quota // cpu` in both modes. Stage 15.1 itself measured that PSO ignores `--cpu`. Only the GUI hid this, by sending `cpu=1` for PSO (`effective_cpu()`). Any other caller lost parallelism for nothing: the CLI after "set cpu to 4", a direct MCP call, or `benchmark_docking.py --cpu`. On the old code, PSO with `cpu=4` on an 8-CPU budget ran 2 workers instead of 8, and with `cpu=8` it ran sequentially.

**Fix:** a new `cpus_per_ligand(algorithm, cpu)` in `common/docking_algorithm.py` returns `cpu` for Vina and 1 for PSO. It is the one rule behind both the server clamp and the GUI's `effective_cpu()`, and so behind the worker maximum.

**Test:** `test_workers_clamped_to_quota_divided_by_cpus_per_ligand` gains PSO rows (`cpu` 4 and 8 → 8 workers).

### 3. Every chat message reset CPUs per ligand, Exhaustiveness and pH
**Files:** `ui/app.py` (`_on_chat_submit`), `services/llm_orchestrator/workflow_llm_parser.py` (`SYSTEM_PROMPT`). Merged with altitude finding #3.
**Triage: fixed immediately, at the root.** *Verified by test against the old code.*

**What went wrong:** `apply_parsed_params()` received the validator's whole output: every default, plus the previous turn's values in a follow-up merge. Phase 15 finding 8 guarded only `docking_algorithm`.

So a user who picked Vina, set CPUs per ligand to 2, Exhaustiveness to 16 and pH to 6.5, and then typed "set center to 10, 20, 30", got 1, 8 and 7.4 back without any notice. The pH reset predates Phase 15. Phase 15 made it matter for the other two, because the CPUs field is new and Exhaustiveness only has an effect in Vina mode.

**Fix:**
- Only the keys the message named reach the form: the follow-up delta's keys, or the first parse's keys. The values come from the validated output, so they are normalised. The `docking_algorithm` special case is gone.
- The first-parse prompt's three examples showed `ph`, `exhaustiveness`, `cpu` and `population` at their defaults, which taught the model to echo them as if the user had named them. They were removed, and the prompt now says to include those fields only when the user states a value. The validator still fills the defaults, so the parsed workflow is unchanged.

**Test:** `test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph` (AppTest).

### 4. An LLM-classified algorithm switch was saved without normalising
**Files:** `nlp_orchestrator.py` (`_dispatch_intent`). Introduced by Stage 15.2.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** `set_docking_algorithm` is in `_SESSION_INTENTS`, so the LLM intent classifier can return it too, with any `name`. Only the deterministic detector guaranteed a canonical name, and the handler saved whatever it got. An LLM-returned `null`, `"gnina"` or `"AutoDock Vina"` would land in the checkpoint and fail later inside moldina-runner, which is the failure Phase 15 finding 7 exists to prevent.

**Fix:** the dispatch branch normalises the name. An unknown one prints "…is not recognised…" and saves nothing.

**Tests:** `test_llm_classified_algorithm_intent_with_bad_name_persists_nothing` (3 cases) and `…_alias_is_normalised`.

### 5. "dock with vina" on a session already using Vina reran everything
**Files:** `nlp_orchestrator.py`. Introduced by Stage 15.2.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** nothing changed, so the handler returned an empty set. `_rerun_pending` then fell back to `CORE_PARAMS`, which means a full rerun: downloads, Meeko preparation, dock, visualize. The help text promises "dock + visualize only".

**Fix:** the rerun form always invalidates through `{"docking_algorithm"}`, which gives dock and visualize.

**Test:** `test_dock_with_current_algorithm_redocks_dock_and_visualize_only`.

### 6. An unrecognised algorithm was asked about again on every follow-up
**Files:** `ui/app.py` (`_on_chat_submit`). Introduced by Stage 15.3.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** the bad name stayed in `parser.parsed_workflow`. Every follow-up merged it back in, the validator flagged it again, and each reply said "Still needed: docking_algorithm". That kept happening even after "use vina" had set the selector, because the chat command never touches the parser.

**Fix:**
- The unknown name is replaced in the parser with the selector's current choice, and it is taken out of `missing_required`.
- The reply says once: "Docking algorithm 'gnina' is not recognised, so Moldina (PSO) stays selected. Use Moldina (PSO) or Vina (Monte Carlo)."

**Test:** `test_chat_parse_naming_an_unknown_algorithm_renders_without_crash`. It now also sends a follow-up and asserts that the follow-up's reply doesn't mention it.

### 7. Benchmark rows recorded the requested worker count, not the one that ran
**Files:** `benchmark_docking.py`, `services/moldina_runner/__init__.py`. Introduced by Stage 15.3 (`--cpu`).
**Triage: fixed immediately.**

**What went wrong:** with `--cpu 4` on a 10-CPU quota, the server runs 2 workers. The default `--worker-counts 2,4,5,8,10` still recorded `parallel_4` through `parallel_10` as separate configurations. Those rows would also feed the "best workers" rule and the per-worker memory figure.

The checked-in 15.3 data is not affected: its multi-CPU runs used worker counts within the quota (5 × 2, 2 × 4).

**Fix:**
- The `run_docking` result gains `workers`, the count actually used after the clamp. This is an additive key; see the compatibility review.
- A new `benchmark_docking.workers_ran(requested, result)` labels each row with that count, prints a `NOTE:` when it differs from the request, adds `requested_workers` to the JSON, and divides the per-worker memory by the real count.
- A failed call, or a server from before this review, falls back to the request.

**Tests:**
- `TestWorkersRan` (4 cases).
- The runner clamp test asserts `result["workers"]`.

### 8. The CLI counted a key an old checkpoint lacks as a change
**Files:** `nlp_orchestrator.py` (`_persist_parameter_updates`). Fixed by Quality #1.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** the CLI hand-rolled its own diff, `old.get(k) != new[k]`. Take a session saved before Phase 15, with no `docking_algorithm` key, and an LLM update that restates `"moldina"`, for example "go back to the PSO search please", which the regex misses. The update counted as changed and queued a needless dock/visualize rerun. The GUI had patched this at two call sites (`_latest_checkpoint_params`, Stage 15.3); the CLI had not.

**Fix:** the CLI uses the shared `compute_changed_params()`, which now fills defaults on both sides (Quality #1).

**Test:** `test_llm_update_restating_default_algorithm_on_old_session_is_no_change`.

### 9. After a cancelled first workflow, a rerun planned only the dependent steps
**Files:** `nlp_orchestrator.py` (`process_workflow`'s cancel path, `_execute_partial_rerun`, `_rerun_pending`). **Pre-existing:** `fbadff4`, 2026-04-14, outside this review's range.
**Triage: fixed immediately, on the owner's decision.** *Found by the real-data run; verified by test against the old code.*

**What went wrong:** answering `n` to "Execute this workflow?" still activates the session, so later parameter commands keep their context. But the activation recorded the *planned* steps as `services_executed`. A later `dock with vina` or `rerun` therefore planned only "Run docking" and failed with "No prepared protein available for docking". This review's first real-data attempt hit exactly that, because a scripted `show state` answered the confirmation prompt.

**Fix:**
- A cancelled run records `services_executed: []` and keeps `services_planned`.
- `_execute_partial_rerun` adds the planned steps that never ran to the plan. This is the CLI twin of the GUI's "steps the last run never got to" rule from Review Stage R1 of Phase 13.
- After a rerun, `services_executed` becomes the union of what really ran.
- The CLI's only other gap of this kind doesn't exist: a failed first workflow never activates a session, and a partial rerun keeps the full executed list.

**Test:** `test_dock_with_vina_after_cancelled_first_workflow_runs_every_planned_step`. It drives the real interactive loop: request, `n`, `dock with vina`, `y`. On the old code it planned `['dock']`.

**Follow-up gap, also fixed:** the search-ligand branch ("Continue now with rerun of affected steps?") checked `services_executed` itself. After this fix it would have silently done nothing on a cancelled session. It now shares `_rerun_pending()`, which also removes a duplicate rerun call. `test_search_ligand_rerun_after_cancelled_first_workflow_still_runs` fails on the old code.

---

## Quality (`/simplify` + 2 from `/code-review`)

### Fixed
1. **Parameter defaults are filled in one place, closing Phase 14 R1's deferred #2** (altitude #1).
   - **The problem:** the "missing key reads as a change" bug had come back three times (2026-10-01, Stage 13.4, Stage 15.3). Each time it was patched at a call site. Phase 15 added a second GUI helper for the old side (`_latest_checkpoint_params`) and a hard-coded `.get("docking_algorithm", "moldina")` in the CLI, and the CLI still had Correctness #8. `job.log` also wrote `docking_algorithm: None -> 'moldina'` on the first save of an old session.
   - **The fix:**
     - New `workflow_definition.with_default_parameters()`.
     - `WorkflowStateManager` stores every checkpoint and draft with all defaults filled in, and upgrades on read (`load_latest_parameters`, `load_draft_parameters`, `load_checkpoint`).
     - `compute_changed_params()` fills both sides. With `old=None` it still reports every key, as before.
     - `_params_as_checkpointed()` and `_latest_checkpoint_params()` are deleted. The redock uses `with_default_parameters()` for what it stores and runs.
   - **Why this is safe:** every reader of the loaded parameters wants them "as they run": the orchestrator's reload, the CLI's `show state` and save-results bundle, and the GUI's Load.
   - **Tests:** `test_key_missing_on_one_side_reads_as_its_default`, `test_old_checkpoint_reads_with_the_defaults_it_ran_with`, `test_restating_a_default_on_an_old_checkpoint_logs_no_change` and `test_saved_parameters_are_stored_full`. Seven state-manager assertions now compare against the defaults-filled dict.
2. **The "PSO uses 1 CPU" rule lived only in the GUI** (altitude #2). Now `cpus_per_ligand()`; see Correctness #2.
3. **"Apply the algorithm only when explicit" was a per-key special case** (altitude #3). It is now "apply only the keys the message named"; see Correctness #3.
4. **`DOCKING_ALGORITHM_LABELS.get(x, x)` was written six times, with two different fallbacks** (simplification #4, reuse #2). Now `docking_algorithm_label()`, which also serves as the selector's `format_func`.
5. **The "missing key means Moldina" rule for results was written twice, and the copies already differed on `None`** (simplification #5, reuse #1). Now `docking_algorithm_of(dock_result)`, used by the results caption and the comparison.
6. **The selector was read from session state three times** (simplification #6). Now `selected_docking_algorithm()`.
7. **Parentheses were stripped in two places** (simplification #3). `normalize_docking_algorithm()` now treats `(`/`)` like `-`/`_`. The two parenthesised aliases and the detector's `re.sub` are gone, and the alias tests gain `"(vina)"` and `"moldina(pso)"`.
8. **The benchmark's PSO-only `population` branch** (simplification #1, #10). `population` is now always sent, because the server already leaves it out of a Vina command. The two argument tests became one parametrised test with a `_sent()` helper.
9. **Literal `"moldina"` defaults** in the orchestrator, server, runner, benchmark and CLI (`/code-review` #9) are now `DEFAULT_DOCKING_ALGORITHM`. The remaining literals are prompt and help text that name the two values.
10. **`compare_jobs()` read each `workflow_results.json` three times** (`/code-review` #10). It now reads each once. `load_job_affinities()` keeps its public signature, and the swallowing `_load_job_name`/`_load_docking_algorithm` are gone.
11. **Test helpers:**
    - The comparator test wrote, read back and rewrote its JSON (reuse #6); `_write_workflow_results` now takes `docking_algorithm`.
    - `_dock_ready_session` was copied into the algorithm test (reuse #3), and this review's own timeout test would have made a third copy. It is now one module-level helper in `test_llm_orchestrator.py`, used by all six tests.
12. **`example_complete_workflow.py`** printed "CPU: 4, Exhaustiveness: 16" for a PSO run as if they applied. It now names the algorithm, passes it explicitly, and says those two are Vina-only. This was found by the compatibility caller check.

### Deferred, with triggers (all three parts in place)
1. **The CLI's LLM write path bypasses the parser's validation gate** (altitude #4).
   - **Problem:** `_handle_update_parameters()` normalises and rejects `docking_algorithm` by hand, because `_persist_parameter_updates()` writes LLM output straight to the checkpoint. The other LLM-extracted fields on that path go unvalidated, and each new enum-valued parameter would need another hand-written clause.
   - **Fix:** a parser-level `normalize_parameter_updates(updates) -> (clean, issues)` built on `get_parameter_issue`, called from `_persist_parameter_updates`.
   - **Recommendation:** defer. There is one enum today, and the clause is correct.
   - **Trigger:** the next enum- or alias-valued parameter, or the next LLM-extracted field that needs validation on this path.
   - **Trigger comment:** `nlp_orchestrator.py`, `_handle_update_parameters()`.
   - **Future Work:** `dev_plan.md` Phase 15.
2. **"CPUs per ligand" text that isn't a whole number silently counts as 1** (exception pass).
   - **Problem:** `effective_cpu()` catches `TypeError`/`ValueError` and falls back to 1, the same way the form reads pH and Exhaustiveness. Typing "four" docks with 1 CPU while the field still shows "four".
   - **Fix:** a `number_input` (min 1), which removes the fallback.
   - **Recommendation:** defer. Phase 16 rebuilds this form in NiceGUI, so doing it in Streamlit now would be done twice.
   - **Trigger:** Stage 16.4, which ports the Advanced section.
   - **Trigger comment:** `ui/session_state.py`, the `effective_cpu()` docstring.
   - **Future Work:** `dev_plan.md` Phase 15.

### Declined, with reasons
- **Write `population` and `search_alg_PSO` in one `if` in `parameters.txt`** (simplification #2). The line order mirrors the command line (`population` among the numeric settings, the flag last). One block would reorder an informational file for no gain.
- **Collapse the three PSO/Vina command tests into one assertion** (simplification #7). Each names one property (PSO flags present, Vina flags absent, shared flags identical). This is the same reasoning as Phase 14 R1's declined parametrisation.
- **A module-level `runner` fixture for `test_moldina_runner.py`** (simplification #8, reuse #5). The five older copies predate Phase 15. Consolidating all six touches eight test classes outside the range, which is churn without a defect behind it.
- **Assert `DEFAULT_DOCKING_ALGORITHM` instead of `"moldina"` in four tests** (simplification #9). Each pins a different layer: the constant, `DEFAULT_PARAMETERS`, the validator and the GUI widget. Changing the default *should* make you look at each of them.
- **One shared `cap10` fixture in `conftest.py`** (simplification #11, reuse #4). The AppTest copy also freezes the widget default, and `tests/conftest.py` is shared with the hermetic suite, where `ui.session_state` (Streamlit) can't be imported.
- **The two `_boom` helpers in `test_ui_app_helpers.py`** (reuse, minor). They are local, with different messages that say which path must not be called.
- **`apply_parsed_params()` re-normalising an already canonical algorithm** (altitude #4, part). It is the guard at the `st.radio` boundary, where an unknown value raises. Keeping the normaliser costs nothing and also covers direct callers.
- **Efficiency:** the agent found no efficiency problem worth fixing. Its four nits are declined as not measurable:
  - the detector's regexes run twice per chat message;
  - one extra `parameters.json` read per CLI "use vina";
  - two deep copies of the defaults per Run or Save;
  - one extra dict lookup per validation.

---

## Noted, not fixed (in `dev_plan.md` Phase 15 Future Work)

These were found during the review and left alone because they are outside its range or not worth a change now. Each has a recommendation in Future Work:
- `--check-health` prints the Ollama endpoint next to any LLM provider (e.g. `openai (http://ollama:11434)`). It is cosmetic and pre-existing.
- `test_moldina_runner.py`'s six copies of the `runner` fixture (declined simplification #8).
- `parameters.txt` records a `cpu` that PSO ignores, for non-GUI callers.

---

## Exception-handling pass

Every `try`/`except`/`raise` added in the range, classified:

| Where | Catch / raise | Verdict |
|---|---|---|
| `moldina_runner::run_moldina_docking` | `raise ValueError` for an unknown algorithm, before any file is written | MCP boundary → explicit error. OK. |
| `ui/session_state.py::effective_cpu` | `TypeError, ValueError` → 1 | Parses free text the user typed, same pattern as the form's pH/Exhaustiveness. Not a band-aid in the §6 sense, but silent. Deferred #2. |
| `job_comparator::_load_docking_algorithm` | `OSError, JSONDecodeError` → default algorithm | It returned "moldina" for an unreadable file. Harmless only because `load_job_affinities` raised on the same file next. **Removed by Quality #10**: the file is read once, a missing file still raises `FileNotFoundError` and a malformed one `JSONDecodeError`, as before. |
| `nlp_orchestrator::_collect_missing_parameters` (legacy loop, Phase 15 Changelog) | No catch; asks again until the value normalises | OK. |

This review added no catches.

---

## Benchmark re-check

Every cell of `docs/BATCH_DOCKING.md` § "Vina benchmark baseline" was recomputed from `docs/benchmarks/2026-10-06_vina_cpus10_mem8g.json`. That covers wall clock, s/ligand, speed-up, peak, mean and per-worker MiB for all six rows, plus the idle baseline (34.1 MiB) and the rule's threshold (134.6 × 1.05 = 141.3 s → 6 workers). The memory check is 34.1 + 6 × 587 MiB ≈ 3.47 GiB.

- **All table cells match.**
- **"CPUs per ligand" table:** the 5 × 2 and 2 × 4 rows match their JSON. The single-ligand speed-ups are 2.0× and 3.4×, and the splits finished 4.6% and 9.1% sooner than 6 × 1, which the docs round to "5–9%".
- **Constants:** `MEASURED_BEST_WORKERS["vina"] = 6` and `MEASURED_PEAK_MIB_PER_WORKER["vina"] = 587` (from 586.5 MiB) hold.
  - The multi-CPU JSONs' sequential rows peak at 587.1 and 587.8 MiB per worker, which would round up to 588.
  - The constant is the `cpu=1` figure, and the caption says "at 1 CPU each", so the two are consistent. `TestMeasuredConstantsMatchBenchmark` checks the `cpu=1` JSON.

---

## Compatibility review (rules §5)

### 1. `run_docking` signature against every caller
The server handler (`services/moldina_runner/server.py`) takes these arguments, all optional after `box_size`: `receptor_path`, `ligand_paths`, `output_dir`, `center`, `box_size`, `cpu`, `population`, `exhaustiveness`, `num_modes`, `energy_range`, `parallel`, `max_workers` and `docking_algorithm`. The server calls `handler(**arguments)`, so an unknown argument is a `TypeError`.

| Caller | Sends | Compatible |
|---|---|---|
| `services/llm_orchestrator/orchestrator.py` | All 12 optional arguments, including `docking_algorithm` | ✅ |
| `benchmark_docking.py::run_docking` | All 12; `population` is now always sent | ✅ |
| `example_complete_workflow.py` | Required arguments + `cpu`/`population`/`exhaustiveness`/`num_modes`/`energy_range`; now also `docking_algorithm` | ✅ |
| `tests/test_docking_stop_integration.py` | Goes through the orchestrator | ✅ |
| `tests/test_orchestrator_integration.py` | A fake keyed by tool name | ✅ |

**Mixed versions:**
- **New callers, old server:** this review added no argument, so the new code works against a runner image from before this review. The real-data run below is that case.
- **Old callers, new server:** `docking_algorithm` defaults to `"moldina"`, so a caller from before Phase 15 gets PSO.

### 2. Result shape against every consumer
Two keys are additive: `docking_algorithm` (15.1) and `workers` (this review).
- **The orchestrator** stores the dict whole in `workflow_results.json` and checkpoints.
- **The GUI caption and the comparison** read `docking_algorithm_of()`, which handles results that lack the key.
- **The benchmark** reads `workers` with a fallback.
- **`ligand_preparer`'s `pose_results` and the visualizer** take the per-ligand `results` entries only, whose shape is unchanged.
- **`parameters.txt`:** a repo-wide grep finds no program that parses it. Its 15.1 header change only affects human readers.

### 3. `common/` change against every image
`common/docking_algorithm.py` gained `docking_algorithm_label`, `docking_algorithm_of` and `cpus_per_ligand`. Its importers are the runner, the orchestrator package, `nlp_orchestrator.py`, `benchmark_docking.py` and `ui/`.
- `common/` reaches the service images only through `docker/base.Dockerfile` (`COPY common/`).
- The runner now imports `cpus_per_ligand`. **`mcp-docking-base` must therefore be rebuilt before `moldina-runner`**, or the runner crash-loops with an `ImportError`. That is the failure the Phase 15 Changelog recorded on 2026-10-05.
- `ui` and `script-runner` run off the `/workspace` mount and pick the change up on restart.

### 4. Compose, env and security
No change in the range or in this review to `docker-compose.yml`, any Dockerfile, the env vars or the ports. All published ports are still bound to `127.0.0.1` (re-checked in `docker-compose.yml`). No new MCP tool and no write capability was added. The tool surface is unchanged apart from the additive result key.

### 5. Real end-to-end run (live services, new CLI and orchestrator code)
- **Setup:** `nlp_orchestrator.py --interactive` in a one-off `script-runner` (this review's code) against the running services through their host-published ports.
- **LLM:** the local Ollama `gemma3:4b`. The checked-in `.env` points at a paid OpenAI model, and the algorithm commands need no LLM.
- **Job:** `job_20261006_040834_a05077`, 6lu7/ATP, box 22 Å at `[-10.8, 12.5, 68.9]`.
- **Results:**
  - **First run:** the workflow (download → prepare → dock, PSO) completed. Best -4.4 in 5.0 s; the dock result records `docking_algorithm: moldina`.
  - **`dock with vina` → `y`:** "Docking algorithm: Vina (Monte Carlo)". The rerun plan was **Run docking** only (the request had no visualize). Best **-5.782** with 15 modes in 125.4 s, matching Stage 15.1's -5.79. `parameters.txt` starts `docking_algorithm = vina` with no `population`/`search_alg_PSO` lines.
  - **Records:** checkpoint rev 6 holds `docking_algorithm = vina` and all 21 keys (Quality #1). `job.log` shows one clean line, `docking_algorithm: 'moldina' -> 'vina'`.
  - **`workers` is `None`:** the running runner image predates this review. This is the mixed-version case working as designed.
- **An earlier attempt found Correctness #9.** Its leftover job folder was deleted.

### Fresh stack (left to the owner)
Rules §5 closes with a fresh `docker compose up`. The live stack runs as a compose project named `af6ad48c…`, not `docking_platform`, so stopping it is the owner's call. Commands:

```bash
docker compose -p af6ad48c09e385d155fffbd9d4b95d009d798f8c0257dfc8f429677e671c1b62 down   # stops the GUI
docker compose --profile dev build script-runner    # mcp-docking-base first (common/ changed)
docker compose build
docker compose up -d
docker compose --profile dev run --rm --no-deps script-runner python nlp_orchestrator.py --check-health
docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/test_docking_stop_integration.py
```

Then repeat the real run: a 2-ligand Vina job with `parallel` on. It should show `workers` in `workflow_results.json` → `results.dock`.

---

## Verification

- **Regression tests fail before, pass after.**
  - **Method:** the new and changed tests were run against `480c9a1`'s production code in a temporary worktree mounted at `/workspace`. Two stubs were added so the test modules import: `with_default_parameters`, which the old code never calls, and `docking_timeout_seconds` with the old rule.
  - **On the old code, 21 tests fail, each because of its bug:**
    - the LLM intent with a bad name or an alias (4);
    - "dock with" the current algorithm, which reran everything (1);
    - the old-session diff (1);
    - the clamp test's 6 rows. All fail on the missing `workers` key. The two PSO rows were also run with that assertion relaxed, and they still fail on the clamp itself: `ThreadPoolExecutor(max_workers=2)`, and no pool at all at `cpu=8`, instead of 8.
    - the shared diff, the defaults upgrade, the `job.log` line and the full storage (4);
    - the Vina timeout (2);
    - the two GUI chat tests (2);
    - the cancelled-first-workflow rerun, which planned `['dock']` (1).
  - **Passing on the old code, as expected:** 4 tests in the same selection, among them the PSO budget and the 300 s floor, which pin behaviour that must not change.
  - **With the fixes:** all of them pass. The worktrees were removed.
- **Hermetic suite:** 1051 passed, 19 skipped (`docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/`), up from 1016. The 19 skips include `test_docking_stop_integration.py`, which skips when the runner isn't reachable by service name, because the live stack runs under another compose project.
- **`ui` tier:** 1202 passed, 12 skipped (`docker compose run --rm --no-deps ui python -m pytest -m "not integration"`), up from 1170.
- **Real data:** the CLI run above.
- **Live browser:** this review changes `ui/app.py` (the chat parse) and `ui/session_state.py`. The running `mcp-ui` still has the old code until it restarts, and the restart is left to the owner. Checklist in the hand-over.
