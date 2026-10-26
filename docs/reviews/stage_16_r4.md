# Review Stage R4: Phase 16 Tier-3 final review

**Date:** 2026-10-26.
**Planning pass:** `C:\Users\radek\.claude\plans\plan-and-implement-stage-cheeky-cat.md`.

## Scope

- **New code: `7c4f5f6..a273ad8`,** Stage 16.8 Parts A and B. This code had never been reviewed (§4: a "final" review must close that gap first). It covers:
  - the benchmark driver and the latency filter;
  - the cutover: compose, Dockerfile, installer, build scripts;
  - the core paths removed with Streamlit;
  - the comment and doc sweep.
- **Phase-wide, with fresh eyes:** `ui/core/`, `ui_nice/`, `ui/*.py`, and the shared-write paths in `common/` and `services/llm_orchestrator/`. Earlier reviews saw this code next to Streamlit; R4 asked what it looks like standing alone.
- **§5 Cross-Service Compatibility:** compose changed in 16.8.

**Owner decisions (2026-10-26):**
- a full rebuild and a fresh `down`/`up` of the live stack (project `af6ad48c…`, no `-v`);
- the chat "Click Run Docking" hint stays in Future Work;
- the dead sync glue is removed;
- the installer gets `--remove-orphans`;
- the Sessions listing gets a cache.

## Method

1. **`/code-review` (high)** over the range: 9 candidate findings, triaged below.
2. **`/simplify`, report-only,** with four angle agents (reuse, simplification, efficiency, altitude), over the range plus `ui/core/` and `ui_nice/`.
3. **An R1–R3 regression table:** every test the three reports cite.
4. **The deferred items' trigger comments.**
5. **The exception pass.**
6. **The R2/R3 invariants.**
7. **§5 on a freshly rebuilt stack.**
8. **A headless end-to-end run on port 3000.**

**Rules restated** (`development_process_rules.md` §4/§6):
- Exceptions are never a band-aid.
- Findings are not manufactured to look thorough; a clean pass is reported as clean.

## Correctness findings

### 1. A chat CPU value that isn't a whole number broke the page (introduced in 16.8). Fixed.

**Symptom:** the form stored `param_cpu` as a string: the default was `"1"`, and a chat parse wrote `str(cpu)`. When 16.8 removed `effective_cpu`'s text fallback, an LLM's `4.0` or `"all"` made `int()` raise, in every redraw (`max_workers`) and in Run Docking.

**Fixed at its source (altitude review):**
- `common.utils.whole_number()` turns `4`, `"4"`, `"4.0"` and `4.7` into `4`, and gives None for `"all"`, `""`, `0` and `True`.
- `WorkflowParser` normalizes `cpu` in `validate_workflow` and `update_parameters`, next to the existing algorithm normalizer. An unparsable value is dropped, so the default or the current value stays. This also guards the CLI → `run_docking` path, which passed the LLM's value through as given.
- The form's default is the int `1`. `apply_parsed_params` stores an int, and `form_cpu`, `collect_form_params` and `load_session_params` share `whole_number()`.
- `ui_nice/setup_tab.py`'s private `_as_int` is gone.

**Tests:**
- `tests/test_ui_core_state.py::test_effective_cpu` (9 cases) and `test_a_chat_cpu_that_is_not_a_whole_number_does_not_break_the_form`. 5 of these fail on `a273ad8` (checked in a worktree).
- `tests/test_llm_orchestrator.py::TestCpuParameter` and `tests/test_utils.py::test_whole_number`.

**Real data:** a CLI run asking for "4.0 cpus" printed `CPUs: 4` and completed.

### 2. The latency filter dropped real work (introduced in 16.8, Part A). Fixed.

**Symptom:** `benchmark_gui_latency.js` ignored any mutation whose target merely *contained* a ripple or tooltip, which includes the clicked button itself. So 16.8's filtered box ± medians (30 / 31 ms) and Load (1,219 ms) were too low.

**Fix:** only changes inside, or to, an ignored element are left out.

**Re-measured** on the live GUI and the same reference session:
- box ± 310 ms filtered; raw 689 / 726 ms, matching 16.8's;
- Load 1,260 ms filtered.

`docs/GUI_GUIDE.md` "Performance" is corrected. The 16.8 JSON keeps its original values.

### 3. Two files of one name in one drop were both kept (pre-existing since Review Stage R3). Fixed.

**Symptom:** for example `set1/a.sdf` and `set2/a.sdf` dropped together. Each is saved in its own upload dir, so both stayed listed, and the run then failed its filename-collision check.

**Fix:** `apply_ligand_uploads` keys the list by file name. A later file of the same name replaces the earlier one, in one pass.

The efficiency review caught that my first version of this fix was quadratic: it took 1.5 s on the event loop for 1,000 files. It is now O(n).

**Test:** `test_two_files_of_one_name_in_one_drop_keep_only_the_last`, which fails on `a273ad8`.

### 4. Existing installs kept `mcp-ui-nice` running (introduced by the 16.8 cutover). Fixed (owner's choice).

**Symptom:** `docker compose up -d` only warns about orphans, so the old NiceGUI container kept serving port 3001 against the same `orchestrator_output/`.

**Fix:**
- the installer's `start_services` and its three launcher scripts use `up -d --remove-orphans`;
- `docs/DOCKER_DEPLOYMENT.md` and `CHANGELOG.md` give the manual step.

**Tests:** `tests/test_installer.py`, updated.

**On the live stack,** 16.8 had already removed the container by hand.

### 5. `docker_build.sh` stopped after the first image (pre-existing since the first commit). Fixed.

**Found by:** R4's own rebuild.

**Cause:** under `set -e`, `((success_count++))` returns status 1 when the counter is 0. So the script exited silently right after building `base`, with no summary and no error, and no service image was ever rebuilt by it.

**Fix:** `success_count=$((success_count + 1))`, and the same for `fail_count`.

**Verified:** the rerun built all 8 images and printed "Successful: 8".

### 6. A settings file holding a JSON list raised (pre-existing since Phase 3). Fixed.

**Found by:** the reuse review.

**Cause:** `ui/settings.load_settings` parsed the file by hand and called `.get` on whatever it got, so a list raised.

**Fix:** it now uses `common.file_io.load_json_dict`, like the rest of the code, which treats a non-object as nothing saved.

`ui/job_history` also loaded JSON by hand; it now uses `load_json_dict` too, which also reads as UTF-8 rather than in the locale's encoding.

### 7. A Delete clicked right after a rename got no dialog (since Review Stage R3). Fixed.

**Found by:** R4's end-to-end run. It reproduced the dead click that 16.8's real-data check had seen once (its row 34) and noted without a reproduction.

**Cause:** a successful rename set `SessionsView._drawn = None` (R3, so the row showed the new name before the re-list), and that forces the next re-list to rebuild every row. A click landing in that window hit a button that was being deleted, and was lost.

**Fix:** the renamed row's label is updated in place (`_Row.label`, `.detail`). The re-list then finds the same sessions (R3 had already written the new name into the listing entry) and keeps the rows.

**Test:** `tests/test_nicegui_sessions.py::test_a_rename_keeps_the_rows_so_a_click_right_after_it_is_not_lost`. It asserts that the row's Delete button is the same element after the rename's re-list and that it opens the dialog. It fails on `a273ad8`.

**Real data:** the rerun did rename → Delete three times, with no pause, and the dialog opened in 0.33–0.55 s each time.

**Still open:** a re-list that really changes the listing (a delete, a run finishing while the tab is shown) still rebuilds every row. That is Deferred #1, whose trigger now names this lost click.

### Declined (code review)

- **The ligand reply quotes the saved name, the protein reply the raw one.** `save_upload` only strips directory parts, which browsers never send.
- **`_scan_jobs`' generality** is folded away anyway (see Quality).
- **The `_resolve_conflict` test helper** is now one shared `tests/ui_fakes.finish_inline`, which runs the GUI's own plan → `write_bundle` → `finish_save` path.

## Quality (`/simplify`)

**Fixed: the dead sync glue (owner's choice).** These existed only for the Streamlit page and had only test callers. They are removed, and the tests now drive the halves NiceGUI calls:
- `Outcome.refresh`. `Outcome` now holds only `error`, since the page redraws after every action.
- `GuiSession.box_picker_last_click` and `box_picker_job_id`, both dead state.
- `chat.submit`'s inline save branch: `run_save` is now required.
- `chat.answer_with_llm`.
- `save_results.save_results()` and `save_chat_reply()`.
- The `results_view` `read_bytes` hook, `ReadBytes`, `_read_path` and `read_job_log()`.
- `sessions.rename_session()` / `delete_session()`.
- `llm_settings.apply_settings()`.

The test helpers that run the GUI's halves inline are `tests/ui_fakes.finish_inline` / `inline_run_save`, plus local `_rename` / `_delete` / `_answer` helpers.

**Fixed, other:**
- **The Sessions listing re-parsed every session on each tab open** (4–6 s for ~190 sessions on a bind mount). The owner chose to fix it now: `ui/job_history.list_completed_and_failed_jobs` caches each parsed entry by its file's `(mtime_ns, size)`, so a re-list parses only the sessions that changed. It returns fresh dicts, because the GUI writes a rename into the entry it holds. It drops deleted sessions, and `_scan_jobs`' generality is folded into it. Tests: `test_a_relist_reads_only_the_sessions_that_changed`, `test_a_listing_entry_changed_by_the_caller_does_not_change_the_next_listing` and `test_a_deleted_session_leaves_the_listing`.
- **One Ollama default endpoint** (`ui.settings.DEFAULT_ENDPOINT`), instead of two literals.
- **Remaining two-GUI wording** in `ui/settings.py`, `ui/core/{form,run_control,sessions}.py`, and a stale `st.radio` reason in `form.py`.
- **`docs/GUI_GUIDE.md` "Code Structure"** follows the removals.

**Deferred, with triggers (all three parts in place):**
1. **Any listing change rebuilds every Sessions row**, about 2,500 elements for 190 sessions (an estimated 0.1–0.3 s on the loop).
   - **Fix:** keep rows by `job_id`, update a renamed label in place, and build the rename editor on demand.
   - **Recommendation:** fix if a click is lost during such a redraw, or a stall is noticed. The rename case, the one the end-to-end run reproduced, is fixed (Correctness #7).
   - **Trigger:** `ui_nice/sessions_tab.py::SessionsView._draw_rows`, plus Future Work.
2. **The parameters summary lists every ligand file and redraws on each Docking Box keystroke** (about 30 KB per keystroke with 1,000 files).
   - **Fix:** list the first few, then "(+N more)".
   - **Recommendation:** fix when a large Batch Processing drop makes typing lag.
   - **Trigger:** `ui/core/form.py::params_summary`, plus Future Work.

**Declined, with reasons:**
- **`apply_geometry`'s inline default** (`box_picker.apply_protein_geometry`). It is a six-line composition of the two production halves and a reasonable synchronous reference, and making it required would add a parameter to about 10 test calls for no behaviour change. Its docstring is accurate.
- **`session_row_label` vs `job_label`** (reuse). The two formats differ on purpose, and unifying them would change how the Sessions rows look.
- **Four one-line lenient UTF-8 reads.** A shared helper isn't worth it on its own.
- **Efficiency, with no measurable cost here:**
  - the Session Log's `ui.code` (logs are at most about 7 KB);
  - about 5 `stat` calls per ligand when loading a result (off the loop);
  - sequential view polls (nothing blocks);
  - row property sets per sync (unchanged values send nothing).
- **The job-id regex in three places.** Each is deliberately as strict as its caller needs; the benchmark runs standalone.

## R1–R3 regression table

Every test the R1, R2 and R3 reports cite as guarding a fix still exists and passes in its tier:
- **The hermetic suite** for `ui/core`, `job_history`, `save_bundle`, `workflow_state_manager` and the benchmark;
- **the GUI tier** for the `test_nicegui_*` tests.

Three names differ from the reports, none of them a lost guard:
- R1's `test_finished_bridge_streams_its_last_lines_before_the_reply` was renamed in Stage 16.4 to `test_finished_bridge_collects_its_last_lines_before_the_reply` (`tests/test_ui_core_run_control.py`).
- R3's `test_listing_rereads_when_the_session_changes_while_shown` was replaced, by design, with `test_a_finished_run_relists_but_a_chat_reply_rereads_only_the_revisions` (R3 Correctness #2).
- The `test_ui_app_helpers` / `test_ui_apptest` mentions in R1/R2 are the deleted Streamlit tier. The fixes they covered are core fixes with core tests (`tests/test_ui_core_chat.py`, `tests/test_ui_core_box_picker.py`). The mapping is in `docs/reviews/stage_16_8_parity.md` §3.

**Trigger comments:**
- R1 #4 (prep-job Stop, `ui/core/box_picker.py`), R2 #1 (file work on the loop, `ui/core/run_control.py::run_docking`) and R3 #2 (pose resend, `ui_nice/results_tab.py`) are still in place and still accurate.
- **R1 #5 (`GuiDeps`) is closed and its comment removed.** Its trigger was "if wiring NiceGUI in 16.3–16.7 threads the values through many call sites". That window passed without it firing, because NiceGUI reads both values from one `PageController`.
- **R1 #6 ("move Streamlit-shaped comments out of `ui/core`") is closed** by 16.8's sweep and R4's last fixes.

## Exception pass

Clean. Every broad catch in `ui_nice/` and `ui/core/` is a GUI boundary that turns a failure into a visible message, and each was justified in R2/R3:
- the chat parse (`parse_failed`);
- the background save;
- the result read;
- the save check;
- the sessions read;
- Compare;
- Settings Apply;
- the validation and rename messages in `ui/core`.

R4 added one catch-free parse, `whole_number()`. It catches `TypeError`/`ValueError`/`OverflowError` around `int(float(value))`, which is input parsing, not a band-aid. The upload writer's `except BaseException: rmtree; raise` is cleanup that re-raises.

## Invariants re-checked

- **No file I/O in any view's `sync()`.** The efficiency review read every `sync()` and found none.
- **Per-client state only.** The one module-level addition, the listing cache in `ui/job_history.py`, holds parsed session metadata shared by all clients, not per-user state. It returns copies and is guarded by a lock, because listings run on worker threads.
- **The `/files` boundary, probed on the live port 3000:**
  - a session file gives 200;
  - `..` to a sibling session, `%2e%2e`, `..%2F`, `saved_results/`, a directory, `uploads/`, a top-level `../` and `//etc/passwd` each give 404.
- **`busy` / `blocked_reason`:** unchanged since R3. R4's removals touched no GUI action wiring.
- **Shared-write permissions, on a Docker named volume** (a real Linux filesystem): root first created a session (dirs 0777, `job.log` 0666, an upload dir). uid 1000 then saved a checkpoint into it, appended to root's `job.log` and saved an upload beside root's. Both runs succeeded, and the log holds both lines.

## §5 Cross-Service Compatibility

- **Rebuild:** `bash docker_build.sh` built all 8 images, after fixing finding 5.
- **Fresh stack:** `docker compose -p af6ad48c… down`, then `up -d --remove-orphans`, without `-v`. All 8 containers were healthy within about 15 s, and the Ollama volume (`gemma3:4b`) was kept.
- **`ui`:**
  - runs as `appuser`;
  - `/health` returns `{"status":"healthy"}`;
  - depends on the five MCP services and Ollama, not `pymol-interactive`;
  - its env carries the five `MCP_*` URLs.
- **Ports:** all 9 published ports bind to `127.0.0.1`.
- **MCP tool signatures:** unchanged in Phase 16. `ui/core`'s callers of the orchestrator are unchanged apart from the cpu normalization, which only narrows values.
- **`--check-health`:** all green.
- **Stop integration test** (`tests/test_docking_stop_integration.py`, `-m integration`, on the live network): 1 passed.
- **CLI workflow:** 6LU7/ATP, local Ollama `gemma3:4b`, "using 4.0 cpus" → `CPUs: 4`. `job_20261026_114222_81de04` completed.

## End-to-end run (headless, port 3000)

**Setup:** Playwright 1.55, headless Chromium 1600 × 1000, on `container:mcp-ui`, against the fresh stack. The script is a scratch extension of 16.8's checklist driver, using `benchmark_gui_browser.NiceGuiDriver`.

The path, session `job_20261026_114407_7d8a3a`:

1. **New session:** Local Files, the reference receptor and 3 ligands, named `r4_e2e`. Run → completed, and Results opened by itself showing "Docked with Moldina (PSO)".
2. **Redock:** Box X 20 → 22, then Run Docking. The Confirm redock plan listed only "Run docking" and "Generate visualization". Confirm → completed.
3. **Algorithm switch:** `use vina`, then Run Docking. Plan: dock + visualize. Confirm → completed in about 9.5 min (3 ligands, sequential), showing "Docked with Vina (Monte Carlo)".
4. **Save-as:** `r4_a` was saved. Saving it again → Overwrite → saved.
5. **Resume:** on a fresh page, Load opened Results with the session. The summary showed Vina (Monte Carlo) and Box 22.0 / 20.0 / 20.0, the chat label was the session's name, and the box picker drew the box at (19.35, 16.79, 29.19).
6. **History:** `show history` posted the table. Restore revision 1 replied "Restored revision 1. …", and the form went back to Box 20 and Moldina.
7. **Delete:** rename then Delete, three times with no pause, each time Cancel; then Confirm. The session left the list.

**Page errors:** none.

**The first run of steps 5–7 failed on three script bugs, none of them in the app:**
- a check on the algorithm radio, which is hidden inside the collapsed Advanced expander;
- "revision 1" taken as the dropdown's first item;
- a New Session click on a fresh page, where it is rightly disabled.

That first run also reproduced the lost Delete click (Correctness #7). Steps 5–7 were rerun from a fresh page after the fix, with `mcp-ui` restarted to load it; the rerun results are above.

**Cleanup:** the test session was deleted through the GUI. Its prep job, the CLI job and the uploads were deleted too, so `orchestrator_output/` holds the same entries as before R4.

## Verification

- **Hermetic suite** (`docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/ -m "not integration"`): 1330 passed, 21 skipped. That is up from 1300 at 16.8: the new tests, minus the removed glue tests.
- **GUI tier** (`docker compose run --rm --no-deps ui python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py`): 170 passed (+1, Correctness #7).
- **Fail-before:** R4's correctness tests were run in a worktree on `a273ad8`.
  - 6 hermetic cases failed there: the cpu parse and the in-drop dedupe.
  - The NiceGUI rename-keeps-rows test failed there.
  - All of them pass now. The worktree was removed afterwards.
- **Live `mcp-ui`** was restarted after the last `ui_nice` change; it is healthy.
