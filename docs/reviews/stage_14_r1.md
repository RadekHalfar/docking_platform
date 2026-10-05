# Review Stage R1 — Phase 14 (Stages 14.1–14.3) — Tier-3 review, widened to close the Phase 13 gap

**Scope:** `db8ad95..41e1ed3`, i.e. everything since Phase 13's Review Stage
R1 fix commit. That is wider than Phase 14's own three stage commits, on
purpose. Three commits landed after Phase 13's R1 and never got review
scrutiny:
- `0a1f267`: Stage 13.4. Chat history is drawn last, and bridges are handled before the layout.
- `fcd97fb`: Stage 13.5. The Docking Box fields are the only box state.
- `b1ff2d2`: Save Session detects "no changes".

Together they are about 650 changed lines, mostly in `ui/app.py`, and only the
owner's live-browser check had covered them. Per `development_process_rules.md` §4 ("a review labeled
final that predates real shipped changes is not final"), the gap is named
here and closed by this review. The owner chose the wider scope. Four of the
correctness findings below (#1, #3, #4, #8) are in that Phase 13 code.

**Method** (`development_process_rules.md` §4):
- `/code-review` (correctness, high effort) over the whole range.
- `/simplify` over the same range, with four parallel angle agents: reuse, simplification, efficiency and altitude.
- A dedicated exception-handling pass.
- A re-check of the docs benchmark table and the `MEASURED_*` constants against the checked-in benchmark JSON.

Every candidate was verified against the real code before triage. Every new
regression test was run against `41e1ed3`'s production code in a temporary
worktree (see Verification).

**Two rules restated, not just pointed at:**
- Exceptions are never used as a band-aid. A broad catch belongs only at a real I/O/protocol boundary, and it must turn the failure into an explicit failed state, never make it vanish.
- No finding is manufactured to look thorough. Candidates that turned out not worth acting on are reported as such (#9, "Declined"), not dressed up.

**Outcome:**
- **Correctness:** 11 findings (10 from `/code-review`, 1 from the real-data check). 10 fixed immediately, 1 accepted as risk.
- **Quality (`/simplify`):** 20 findings after dedup.
  - 9 fixed (some overlap with correctness fixes).
  - 3 deferred with written triggers.
  - 8 declined with reasons.
- **Exception pass:** clean.
- **Benchmark re-check:** one rounding error in the docs, fixed. The benchmark was re-run after correctness #2, and the constants hold.
- **Accepted as risk:** 1, beyond the triaged findings.

Test results are in "Verification" at the end.

---

## Correctness (`/code-review` + real-data check)

### 1. A job that finished during a script run sat unhandled until the next click
**Files:** `ui/app.py` (`_process_bridges`, the bottom-of-page poll). Introduced by Stage 13.4.
**Triage: fixed immediately.** *Verified by repro.*

Since 13.4, a finished bridge is handled in one place only: `_process_bridges()` at the top of the script. The poll at the bottom reran only while a bridge was *still* running.

**What went wrong:** a job that ended between those two points (during the sidebar, chat or panel rendering) was never handled. It got no "Workflow completed successfully!" reply, `_job_done` and `pending_changed_params` were not updated, and a box-picker receptor was not applied. All of that waited until the user's next interaction.

**Fix:**
- `_process_bridges()` returns whether a bridge was running when it looked.
- The page polls once more in that case, so the next run handles the job.
- This always terminates: a run that starts with no bridge running doesn't poll.

**Partly not confirmed:** part of the candidate said that clicking "Load receptor for box picking" in that state would lose a pending geometry command. That isn't so: the click's own rerun runs `_process_bridges()` first.

**Test:** `test_job_finishing_mid_run_is_handled_by_one_more_poll`. Its fake bridge finishes while the page streams its log.

### 2. Benchmark wall clock included the stats sampler's shutdown
**Files:** `benchmark_docking.py` (`main`, now `run_timed`). Introduced by Stage 14.2.
**Triage: fixed immediately, and the 14.3 benchmark re-run.** *Verified by reading + test + re-run.*

**What went wrong:** `elapsed` was taken after `sampler.stop()`. Stopping joins the sampler thread, which waits out an in-flight `docker stats` call of about 1–2 s, up to the 15 s timeout. Each configuration's wall clock could therefore grow by a random amount. On runs of about 23–24 s, that is the same size as the 5% margin Stage 14.3's rule used to pick `MEASURED_BEST_WORKERS = 8` (24.3 s against 23.4 s).

**Fix:** a new `run_timed(call, sampler)` takes the wall time inside the sampler's context, before it stops. `main()` uses it.

**Re-run:** the same 50-ligand command was run again after the fix (see "Benchmark re-check"). 8 workers was then the fastest outright (22.8 s against 23.1 s at 10), and the worst per-worker memory was 611.1 MiB. So the constants hold at 8 and 612, but now on evidence that isn't biased.

**Tests:** `TestRunTimed` (3 cases; a sampler whose shutdown takes 0.5 s must not count).

### 3. The picker's − / + on an unset box size seemed to do nothing
**Files:** `ui/app.py` (`_step_box_size`). Introduced by Stage 13.5.
**Triage: fixed immediately.** *Verified by repro.*

**What went wrong:** with the Box size fields empty, **X +** wrote X = 21 only. The viewer can draw a box only when all three fields parse, so it kept showing the 20 Å placeholder, and the button appeared dead. A later atom click then wrote 20 into all three fields, which silently discarded the step.

**Fix:** while the viewer shows its placeholder, a step starts from that placeholder and writes all three fields. In this case **X +** gives 21 / 20 / 20.

**Tests:**
- `test_size_step_with_size_unset_draws_the_stepped_box` (AppTest).
- `test_step_box_size_steps_from_the_placeholder_when_undrawable` (4 cases).

### 4. A click on an undrawable box docked a box other than the one shown
**Files:** `ui/app.py` (`_render_box_picker`, `_apply_viewer_click`). Introduced by Stage 13.5.
**Triage: fixed immediately.** *Verified by repro.*

**What went wrong:** the viewer treated "unset *or* not positive" as "draw the placeholder", but the click handler filled in the placeholder only when the size was *unset*. With fields `0, 20, 20`, the viewer showed 20 × 20 × 20, a click kept the 0, and "the box picked is the box docked" (13.5's own promise) didn't hold.

**Fix:** one helper, `_drawable_box_size()`, is used by the render, the click and the step. The placeholder is now also passed to the viewer explicitly from Python (`_PICKER_DEFAULT_BOX_SIZE`) instead of being left to the component's own `|| [20, 20, 20]`. The constant that is drawn is therefore the same constant that gets written (altitude finding #4).

**Tests:**
- `test_click_on_undrawable_box_docks_the_box_shown` (AppTest).
- `test_apply_viewer_click_replaces_an_undrawable_size_with_the_placeholder`.
- `test_undrawable_box_size_shows_placeholder_without_crash` now asserts the placeholder that was passed.

### 5. A malformed compose file could crash GUI startup
**Files:** `ui/worker_cap.py` (`detect_moldina_worker_cap`). Introduced by Stage 14.1.
**Triage: fixed immediately.** *Verified by test against the old code.*

**What went wrong:** 14.1 replaced the bare `except Exception` with narrow catches, which was right, but the narrowing missed two failures. The function runs at import time of `ui/session_state.py`, and its docstring promises it never blocks startup.
- `cpus: .inf` or `'1e999'` made `int(float(...))` raise `OverflowError`.
- A non-UTF-8 file raised `UnicodeDecodeError`.

Neither was caught. On `41e1ed3` the non-UTF-8 test crashes with the uncaught error.

**Fix:**
- The file is opened with an explicit `encoding="utf-8"`, and `UnicodeDecodeError` becomes a fallback reason.
- The quota is checked as `0 < quota < inf`, which also rejects NaN, before converting.

**Tests:** `test_falls_back_on_infinite_or_nan_limit` (3 cases) and `test_falls_back_on_non_utf8_file`.

### 6. A fractional CPU limit below 1 fell back to the whole machine
**Files:** `ui/worker_cap.py`. Introduced by Stage 14.1.
**Triage: fixed immediately.** *Verified by reading the server's rule.*

**What went wrong:** with `cpus: '0.5'`, `int(0.5) = 0`, so the GUI took the fallback path. It offered up to 20 workers with a default of 8 against half a CPU, and its warning said `'0.5'` was "not a positive number".

**Fix:** the GUI now mirrors the server's own rule from `services/moldina_runner/__init__.py`: `quota // period`, then `max(1, min(quota, os.cpu_count()))`.
- `0.5` → 1.
- A limit above this host's cores is clamped to them, so `cpus: '10'` on a 4-core host gives 4. This was altitude finding #1's concrete case, and it previously showed 10 with no warning.

Both containers share the host, so the `ui` container's `os.cpu_count()` is the same number the runner sees.

**Tests:**
- `test_fractional_limit_below_one_is_one_worker`.
- `test_limit_above_machine_cores_is_clamped_to_them`.
- The existing fractional test now pins `cpu_count`.

### 7. The caption recommended 8 workers whatever the limit
**Files:** `ui/worker_cap.py` (`describe_worker_cap`). Introduced by Stage 14.3.
**Triage: fixed immediately.** Merged with altitude finding #2.

**What went wrong:** at `cpus: '4'`, the widget's maximum is 4 and the default is 4, yet the caption still said "Benchmarked best: 8 workers". The figure was 10-CPU data presented as if it applied.

**Fix:**
- A new `MEASURED_AT_CPUS = 10` stores the limit the benchmark ran at.
- When the detected cap differs from it, or comes from the fallback, the caption says "Benchmarked best at a 10-CPU limit, which may not hold here: 8 workers …".
- The docs tell the reader to re-benchmark after changing the limits.

**Test:** `test_compose_cap_other_than_benchmarked_names_the_conditions`.

### 8. Two comments pointed at `render_chat()` for work that moved
**Files:** `ui/app.py` (`_handle_redock_request`, `_resume_session`).
**Triage: fixed immediately.**

Stage 13.4 moved the completion sync into `_process_docking_bridge()`, but two comments still sent a maintainer to `render_chat()`. Both comments are corrected.

### 9. Benchmark: `--stats-interval 0`, and one idle baseline for all configurations
**Files:** `benchmark_docking.py`.
**Triage: accepted as risk. Recommendation: leave as is.**
- **Interval 0:** this does not spin. Each sample is a `docker stats --no-stream` call that takes 1–2 s, so the loop is bounded by that. Continuous sampling is a legitimate choice.
- **Idle baseline:** it does move between runs. It was 37.6 MiB in the 14.3 run and 45.3 MiB in this review's re-run. Within a run, any growth would inflate *later* configurations' per-worker figure. But the figure that sets `MEASURED_PEAK_MIB_PER_WORKER` came from the first two configurations in both runs, and it is shown only as a caption, not used as a limit. Not worth a per-configuration baseline.

### 10. The sampling exception tuple was written twice
**Files:** `benchmark_docking.py`.
**Triage: fixed immediately.**

The sampler and the idle-baseline read could drift apart, so that one crashes where the other counts. There is now one `STATS_ERRORS` tuple.

### 11. "limited to 1 CPUs"
**Files:** `ui/worker_cap.py`.
**Triage: fixed immediately.** *Found by the real-data check:* #6 made a cap of 1 reachable, and the caption didn't handle the singular. It does now, and the caption test includes the 1-CPU case.

---

## Quality (`/simplify`)

### Fixed
1. **The docking bridge's four outcome branches** each set `_job_done` and appended a message (simplification #7). Each branch now only picks a `reply`, and one place marks the run as handled.
2. **The benchmark's configuration loop** (simplification #8, efficiency #1):
   - It now uses `ContainerStatsSampler`'s own context manager, through `run_timed`.
   - `stats` is attached once instead of in both branches.
   - The `error = None` / `continue` juggling is gone.

   This is also correctness #2.
3. **Collect form → update params → copy** was repeated in three places (altitude #3, second half). There is now a single `_sync_form_into_params()`.
4. **The placeholder box size was defined in Python and in JS** (altitude #4). It is now passed from Python; see correctness #4.
5. **The measured constants silently depended on one compose configuration** (altitude #2):
   - `MEASURED_AT_CPUS` plus the caption change (correctness #7).
   - A new test class, `TestMeasuredConstantsMatchBenchmark`, re-derives `MEASURED_BEST_WORKERS` (with the documented 5% rule) and `MEASURED_PEAK_MIB_PER_WORKER` (ceil of the worst per-worker figure) from both checked-in benchmark JSONs, so the constants can't drift from the evidence.
6. **The GUI re-derived the server's CPU rule differently** (altitude #1, partly). It now uses the same floor and `min` rule; see correctness #6. Asking the server is deferred (below).
7. **Test duplication** (reuse #1, #2; simplification #1, #2):
   - One `_prep_done_bridge()` replaces four copies of the same fake.
   - The two `..._without_rerun` box-picker tests are folded into the originals they copied, which now assert `page_reruns == []`.
   - The Session-State-warning test reuses `_receptor_loaded_with_box`.
   - `_load_history_job` is defined once, above its first use, and reused.
8. **A stale helper name** (simplification #3): `_picker_with_matching_fields` → `_picker_with_box_set`, since there is no picker copy left for the fields to "match".
9. **"up to ~612 MiB" claimed more than a sampled lower bound shows** (altitude #5, partly). The caption and docs now say "sampled peak ~612 MiB". Exact peaks are deferred (below).

### Deferred, with triggers (all three parts in place)
1. **Ask the server for its worker cap instead of parsing compose** (altitude #1).
   - **Problem:** the GUI holds a copy of what `MoldinaRunner._detect_effective_cpu_count()` computes. Since correctness #6, the copy uses the same rule and the same host. It can only drift if moldina-runner stops running on this host with this compose file, e.g. a remote or HPC runner or a renamed service.
   - **Fix:** have the runner report its effective worker count over MCP, and keep the compose read as the fallback. That is an MCP tool change, so it would trigger a cross-service compatibility review.
   - **Recommendation:** do it only on the trigger.
   - **Trigger comment:** `ui/worker_cap.py`, the `detect_moldina_worker_cap()` docstring.
   - **Future Work:** `dev_plan.md` Phase 14.
2. **Default-filling belongs in the shared diff, not at each GUI call site** (altitude #3).
   - **Problem:** `_params_as_checkpointed()` is right for both GUI callers today (redock and Save Session). Each new GUI path that diffs or stores params would have to remember it, and the bug has already shown up twice (2026-10-01 and the Save fix).
   - **Fix:** normalize inside `compute_changed_params` or in `WorkflowStateManager` on save. That also changes the CLI, which is why it is not done in a review pass.
   - **Recommendation:** do it when a third GUI caller of `compute_changed_params` or `save_draft_parameters` is added, or when a "changed to None" diff shows up on the CLI.
   - **Trigger comment:** `ui/app.py`, the `_params_as_checkpointed()` docstring.
   - **Future Work:** `dev_plan.md` Phase 14.
3. **Exact peak memory from the kernel** (altitude #5).
   - **Problem:** polled peaks are lower bounds. cgroup v2 `memory.peak` exists in moldina-runner (kernel 6.18), but it covers the container's lifetime (5178 MiB at review time, against a sampled 4963 MiB at 10 workers). It can be reset only per open file, so a plain read can't be attributed to one configuration.
   - **Fix:** keep `memory.peak` open in a `docker exec` for each configuration; reset it at the start and read it at the end.
   - **Recommendation:** leave it until the per-worker figure decides a limit, such as a memory-based worker cap, rather than a caption.
   - **Trigger comment:** `benchmark_docking.py`, the `ContainerStatsSampler` docstring.
   - **Future Work:** `dev_plan.md` Phase 14.

### Declined, with reasons
- **One "detail" string for the box-picker bridge's outcomes** (simplification #7, second half). The "no PDB path" case has its own wording in both the panel and the chat, so the shared pattern breaks on one of three branches. As written, it reads clearly.
- **Drop the Session-State-warning test as vacuous** (simplification #4). It is a cheap guard. The Docking Box `text_input`s take their value from session state only, and adding `value=` back would bring the warning straight back.
- **Parametrize the nine fallback tests** (simplification #5). Each named test reads as one line of the spec. This would be churn without a defect behind it.
- **Compute the `param_max_workers` default in `init()`, not at import** (simplification #6). It only saves two lines in two tests, and it would move the session-load clamping path for no user-visible gain.
- **Derive `WorkerCap.source` from `reason`** (simplification, minor). The explicit field reads better in the caller and in the tests.
- **Compute `describe_worker_cap()` once instead of per rerun** (efficiency). It is two string formats, which is not measurable.
- **Merge `_load_picker_receptor` and `_receptor_loaded_with_box`** (simplification #3, rest). Both are needed: one loads the receptor directly, the other goes through the bridge path. The stale name was the real issue, and it is fixed.
- **Reuse:** the reuse agent found nothing in the production code to replace with an existing helper. Its two test findings are fixed above.

### Accepted as risk
- **The caption names `docker-compose.yml` even when the cap was clamped to this host's cores** (e.g. `cpus: '64'` on 20 cores shows "limited to 20 CPUs (docker-compose.yml)"). The number is right; only its attribution is loose, and only when compose asks for more cores than the host has. **Recommendation: leave it.**

---

## Exception-handling pass

Every `try`/`except` added in the range, classified:

| Where | Catch | Verdict |
|---|---|---|
| `ui/worker_cap.py::detect_moldina_worker_cap`, file read | `FileNotFoundError`, `OSError`, `UnicodeDecodeError`, `yaml.YAMLError` → `WorkerCap(source="fallback", reason)` | File/parse boundary → explicit fallback with reason. OK (the gap in it was correctness #5). |
| same, key lookup | `KeyError, TypeError` → fallback | Lookup in parsed external YAML. OK. |
| same, conversion | `TypeError, ValueError` → fallback | Parse of an external value. OK. |
| `benchmark_docking.py::ContainerStatsSampler._run` | `STATS_ERRORS` → counted in `errors` + `last_error`, reported per configuration | Subprocess boundary → explicit count. Anything else propagates (tested). OK. |
| `benchmark_docking.py::main`, idle baseline | `STATS_ERRORS` → `SystemExit` with the reason | Fails loudly instead of reporting meaningless numbers. OK. |
| `benchmark_docking.py::run_timed` | `except Exception` → `(None, message, elapsed)`, printed and stored as an error row | Network/MCP boundary for one benchmark configuration. Moved from `main()`, same behaviour. OK. |

13.4, 13.5 and `b1ff2d2` added no catches. This review removed one:
`_step_box_size`'s `except (ValueError, TypeError)` is gone, because
`_drawable_box_size()` decides the fallback. 14.1 removed the bare
`except Exception: pass` around the compose read, which was the band-aid
14.1 was planned to fix. No band-aids found.

---

## Benchmark re-check

Every cell of the `docs/BATCH_DOCKING.md` "Benchmark baseline" table was
recomputed from `docs/benchmarks/2026-10-05_moldina_cpus10_mem8g.json`:
wall clock, s/ligand, speed-up, peak, mean and per-worker MiB for all six
rows, plus the rule's threshold (24.6 s) and the memory check (≈ 4.8 GiB).

- **All table cells match.**
- **One mismatch outside the table:** the idle baseline is 39,374,028 B = 37.55 MiB, which prints as **37.6**, not the 37.5 that the docs and `dev_plan.md` said. Corrected in both, and in the memory-check sentence.
- **Re-run after correctness #2:** the same command was run again into `docs/benchmarks/2026-10-05_moldina_cpus10_mem8g_r1_rerun.json` (idle 45.3 MiB, 300/300 succeeded, 0 failed samples).

  | Workers | 1 | 2 | 4 | 6 | 8 | 10 |
  |---|---|---|---|---|---|---|
  | Wall clock (s) | 115.0 | 56.9 | 30.5 | 24.8 | **22.8** | 23.1 |
  | Peak MiB/worker | 611.1 | 563.1 | 541.1 | 536.0 | 521.1 | 516.1 |

  The rule gives 8 again: 8 is the fastest, and 6 is outside 5%. The worst per-worker figure, 611.1, rounds up to 612. The constants are unchanged.
- **Docs:** `BATCH_DOCKING.md` keeps the original table as the record and adds a "Re-run (Review Stage R1)" paragraph.
- **Test:** `TestMeasuredConstantsMatchBenchmark` checks the constants against both JSONs.
- **Cleanup:** the re-run's `orchestrator_output/_benchmark_9e436f97/` was deleted.

---

## Verification

- **Regression tests fail before, pass after.**
  - **Method:** the new and changed tests were run against `41e1ed3`'s production code, in a temporary worktree mounted into the `ui` image. The only change there was adding `MEASURED_AT_CPUS = 10`, so the test files import.
  - **On the old code, 25 tests fail, each because of its bug:**
    - The mid-run poll gave no completion reply.
    - The step left the placeholder (5 cases).
    - The click kept the 0.
    - `0.5` fell back to the whole machine.
    - The clamp to the core count was missing.
    - `.inf` / `.nan` / `'1e999'` raised or gave the wrong reason.
    - The non-UTF-8 file crashed with an uncaught `UnicodeDecodeError`.
    - The caption cases failed.
    - `run_timed` was missing.
  - **With the fixes:** all of them pass. The worktree was removed.
- **Full hermetic suite:** 887 passed / 17 skipped / 2 deselected (`docker compose --profile dev run --rm --no-deps script-runner python -m pytest -m "not integration"`), up from 869.
- **`ui` tier:** 1007 passed / 12 skipped (`docker compose run --rm --no-deps ui python -m pytest -m "not integration"`), up from 985.
- **Real data, worker cap.** In the live `mcp-ui` container, against the real `docker-compose.yml`: `WorkerCap(cap=10, source='compose')` → default 8, and the caption shows no conditions note. Temporary compose files gave:

  | `cpus` | Cap | Default | Caption |
  |---|---|---|---|
  | `'0.5'` | 1 | 1 | Conditions note shown |
  | `'4'` | 4 | 4 | Conditions note shown |
  | `'64'` | 20 (this host's cores) | 8 | Conditions note shown |

  This check found correctness #11.
- **Real data, benchmark:** the full 50-ligand re-run above.
- **Live browser:**
  - The 14.1–14.3 caption and default 8 were checked by the owner after `docker compose restart ui`. **Passed 2026-10-05.**
  - This review changes `ui/app.py`: the poll, the box − / + and the click. The running `mcp-ui` still has the old code until it restarts. The restart is left to the owner because it kills any GUI-driven run in progress. Checklist in the hand-over.
