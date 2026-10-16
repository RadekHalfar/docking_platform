# Review Stage R3 — Phase 16 (Stages 16.6–16.7) + Cross-Service Compatibility Review

**Scope:** `b221ca9..0fd1d9f`, everything since Phase 16's Review Stage R2
fix commit:
- `97817a6`: Stage 16.6, the NiceGUI Results tab, the validated `/files` download route, saves off the event loop.
- `0fd1d9f`: Stage 16.7, the Sessions tab and the Settings dialog, Load/Delete/Restore waiting for `PageController.busy`.

Nothing else landed after R2, so there is no review gap. 29 files, about 3.5k lines. The only `services/` change is `job_comparator.py`'s duplicate-name labels.

Two items were handed to R3 by 16.7:
- the Plotly page error on leaving the Results tab;
- checking the AppTest tier's `app` fixture for the `LLM_API_KEY` leak 16.7 found in the NiceGUI harness.

Planning pass: `C:\Users\radek\.claude\plans\joyful-nibbling-quasar.md`.

**Method** (`development_process_rules.md` §4):
- `/code-review` (correctness, high effort) over the range: 10 findings.
- `/simplify`, run report-only, with four parallel angle agents: reuse, simplification, efficiency and altitude.
- **R2's invariants re-checked on the new code:**
  - (a) the event loop: every Results / Sessions / Settings handler and `poll()` traced into `ui/core`, with the slow reads timed on the live stack's Docker Desktop bind mount;
  - (b) per-user state: a grep for module-level mutables;
  - (c) the `/files` route boundary, re-attacked;
  - (d) the new write paths (rename, delete, settings) on a Linux named volume, root then uid 1000;
  - (e) the busy / running rules.
- The exception pass over every `try/except` the range added.
- Rules §5 in full, closed by a fresh stack and real runs from the CLI and from NiceGUI.

**Two rules restated, not just pointed at:**
- **No band-aid exceptions.** A catch belongs at a boundary that reports the failure: a background task's top, a user action, an optional display read. It never hides a bug inside the logic.
- **No manufactured findings.** Clean areas are reported as clean; nothing is padded.

**Owner decisions:**
- **Fresh stack:** a full rebuild, then `down` (without `-v`) and `up -d` under the live project name. This also ended the restart loop of the two UI containers since the Docker restart.
- **Plotly:** first "draw the chart only while Results is shown". The review found that a user's tab click hides the chart in the browser before the server hears of it, so that option can't help. The owner then chose a resize guard in the browser (Correctness #10).
- **A same-named ligand drop** replaces the listed file, with a chat note (16.7's open finding; Correctness #1).

**Outcome:**
- 13 correctness fixes:
  - 8 from `/code-review` (#2, #4–#8, #11, #12);
  - 1 from the exception pass (#9);
  - 1 from the real-data check (#3);
  - 3 handed over by 16.7 (#1, #10, #13).

  Each has a test that fails on `0fd1d9f`, except #10 (browser-only, verified in the real-data check) and #13 (a fixture change).
- 4 quality fixes.
- 2 deferrals with triggers.
- The rest declined with reasons.
- §5 clean.

---

## Correctness

### 1. A same-named ligand drop was listed twice, and the run failed (16.7's open finding; owner's choice of fix)
- **Symptom:** New Session keeps the inputs, and each NiceGUI drop is appended (R2's choice). A second drop of `crystal_ligand.mol2` listed two files of one name, and the run failed at the orchestrator's "Local ligand filename collision" check.
- **Fix:** `ui.core.uploads.apply_ligand_uploads(append=True)` replaces a listed file whose name matches a dropped one. The reply adds "(replaced the earlier …)". Streamlit, which never appends, is unchanged.
- **Tests:** `test_a_dropped_ligand_named_like_a_listed_one_replaces_it` (hermetic), `test_dropping_a_listed_ligand_again_replaces_it` (NiceGUI). The real-data check dropped the file twice and the run docked one ligand.

### 2. The Sessions tab re-read every session on each chat reply: 4–6 s per read (16.7, `/code-review`)
- **Cause:** the tab's change key included the message count, and every refresh ran `list_sessions()`.
  - It parses every session's `workflow_results.json` twice, once for each list.
  - **Measured** in the `ui-nice` image on the live bind mount: 163 completed and 11 failed sessions took 4.3–5.7 s.
  - It ran off the loop, but on every chat message while the tab was open, and the listing is no longer capped at 10.
- **Fix:**
  - The tab now has two keys:
    - a listing key (the active job, `job_done`). It re-reads the sessions;
    - the change key. On a chat reply it re-reads only the revisions (`_read_listing(..., previous)` keeps the sessions).
  - No chat command changes the session list: rename and delete are this tab's own actions, and those re-list.
  - `ui.job_history._scan_jobs` sorts each file into the first matching listing in one pass. `list_completed_and_failed_jobs()` serves `sessions.list_sessions()`.
  - Rows are rebuilt only when the sessions differ by value.
- **Also:** a results file that isn't a JSON object raised `AttributeError` and lost the whole listing. It is now skipped.
- **Tests:**
  - `test_a_finished_run_relists_but_a_chat_reply_rereads_only_the_revisions` replaces 16.7's `test_listing_rereads_when_the_session_changes_while_shown`, which pinned the old behaviour.
  - `TestListCompletedAndFailedJobs` (one parse per file; the non-object file).

### 3. A Load right after a rename resumed the old name (16.7; found in this review's real-data check)
- **Cause:** a row's Load passes the listing entry it was drawn with. After a rename the rows kept the old entry until the re-list finished, 4–6 s on the live mount. A Load in that window ran `resume_session` with the old `job_name`, overwriting the renamed `state.job_name`. The driver hit it by clicking Load about 2 s after the rename.
- **Fix:** `sessions.apply_session_rename` also updates the listing entry. The tab redraws its rows at once (`_drawn = None`), so the row shows the new name immediately. Streamlit's re-list is synchronous, so it never hit this.
- **Tests:** `test_a_load_before_the_relist_after_a_rename_uses_the_new_name` (holds the re-list open), and the listing-entry assertion in `test_rename_and_delete_come_in_two_halves`.

### 4. Rename ran during a delete's `rmtree` (16.7, `/code-review`; altitude #2)
- **Cause:** `blocked_reason()` covered Load, Restore and Delete, but Rename tested nothing. A rename of the session being deleted writes the lock file and `workflow_results.json` into the directory `rmtree` is removing: either "Delete failed: Directory not empty", or a stub dir that lists as a broken session.
- **Fix:** Rename and its Save are off while `controller.busy` is set, and `_on_rename_save` re-checks.
- **Test:** `test_load_delete_and_restore_wait_for_a_save` now also asserts `rename-*` and `rename-save-*`.

### 5. A name being typed was lost when the list was re-read (16.7, `/code-review`)
- **Symptom:** a run finishing while the Rename field was open re-listed, rebuilt the row, and refilled the field with the old name.
- **Fix:** `_draw_rows` carries the typed value over. Rows are also kept when the sessions are unchanged (#2).
- **Test:** `test_a_name_being_typed_survives_a_relist`.

### 6. A failed read of the sessions spun forever, and a caught one stayed on screen (16.7, `/code-review`)
- **Cause:** `_refresh` caught only `OSError` / `ValueError`. Anything else killed the task with `refreshing` still True, and the key already counted as refreshed, so nothing retried. A caught error went into `self.error` and stayed above a later good listing.
- **Fix:** the task's top catches any exception: it is logged, shown as `read_error`, and the spinner stops. The next read re-lists, and a good read clears the error. It does not retry at once, so a lasting failure can't loop.
- **Test:** `test_a_failed_listing_is_shown_and_the_next_read_clears_it`.

### 7. A failed results read left the viewer on its previous session for good (16.6, `/code-review`)
- **Cause:** `ResultsView._load` caught only `OSError`. Any other exception, such as a malformed result, killed the task with `_loading` still set, and that key was never loaded again.
- **Fix:** the task's top catches any exception and shows "Could not read the results: …".
- **Test:** `test_a_result_that_cannot_be_read_is_said_so`.

### 8. A failed save check left Save off with no reason (16.6, `/code-review`)
- **Cause:** `_refresh_reason` had no catch. If `save_block_reason` raised (e.g. another uid's unreadable manifest), the task died with `_reason_loading` set. The button stayed off, no reason was shown, and the check never ran again.
- **Fix:** the failure becomes the reason shown.
- **Test:** `test_a_failed_save_check_is_shown_not_left_pending`.

### 9. An unreadable `job.log` during a run stopped every redraw (16.6, exception pass)
- **Cause:** `ResultsView.poll()` let the read's `OSError` through, and `PageController.tick()` awaits each view's `poll()` before `self.sync()`. Every tick of the run raised before the redraw, so progress, Stop and the other views stopped updating.
- **Fix:** `poll()` keeps the log as last read and logs a warning.
- **Test:** `test_an_unreadable_log_keeps_the_last_one_and_the_tick_going`.

### 10. Plotly logged "Resize must be passed a displayed plot div element" on leaving Results (16.6; handed over by 16.7; owner's choice of fix)
- **Cause:** NiceGUI 3.17.1's Plotly element (`plotly.js`) calls `Plotly.Plots.resize` from a `ResizeObserver`. That also fires when the browser hides the chart: the main tabs' keep-alive panel detaches it, and the inner Session Viewer tab hides it. Plotly throws on a div it can't measure.
- **Why not the first option:** a user's tab click hides the panel in the browser before the server hears of it, so removing the chart server-side comes too late.
- **Fix:** `results_tab.PLOTLY_RESIZE_GUARD`, a module script added to the page head, wraps `Plotly.Plots.resize` to skip a div that isn't connected or displayed. The observer fires again when the chart is shown, so the chart still follows the splitter. The constant's comment says to re-check it on a NiceGUI upgrade.
- **Verified:** in the real-data check the page moved Setup → Results → Sessions → Results → Compare Sessions → Session Viewer → Setup. That gave 0 resize errors, against five on the same moves in 16.7's check. `Plotly.Plots.resize` in the page is the guard. The User fixture runs no browser, so there is no unit test.

### 11. The Results status kept a renamed session's old name (16.6, `/code-review`)
- **Cause:** the status line used the name the result was read with, and redrew only when the load key changed. Streamlit re-reads on every rerun.
- **Fix:**
  - For the active session the name comes from `state.job_name`, which names `state.job_id` and which a rename updates.
  - `sync()` retexts the status line.
  - The line is built by one `_status_text()` for the running, completed and viewed cases.
- **Test:** `test_a_rename_of_the_viewed_session_follows_into_its_status`. The real-data check saw it too.

### 12. Reopening Settings kept a model picked but not applied (16.7, `/code-review`)
- **Symptom:** pick model B, then Close. On reopen the select still showed B, because it was still listed. A later Apply, say of a new endpoint, saved B.
- **Fix:** `_fill` clears the select, so `_sync` selects the session's model.
- **Test:** `test_reopening_drops_a_model_picked_but_not_applied`.

### 13. The AppTest tier's `app` fixture could pass the real `LLM_API_KEY` to tests (handed over by 16.7)
- **Cause:** as in the NiceGUI harness before 16.7, `docker compose run` passes `.env`, and the fixture set only the provider and the endpoint.
- **Fix:** it also sets `LLM_MODEL` and empties `LLM_API_KEY` / `LLM_BASE_URL`. A grep of the other fixtures found no other gap: `test_settings.py` already deletes all five variables.

---

## Focus areas

### (a) The event loop
- **Clean in the new code.** Every disk or network call is in `run.io_bound`:
  - `read_results_data`, `job_log_if_changed`, the save block reason;
  - the listing, the revisions, Delete's `rmtree`, the comparison;
  - the Ollama model list, `persist_settings`.
- Each read has a stale-result guard.
- **Load and the Save button's check run on the loop:** `resume_session`, and `plan_save` → `save_block_reason`. They take about 55 ms for the largest (1.2 MB) session. That is the same kind of work as R2 Deferred #1, so it is deferred with it (Deferred #1 below).

### (b) Per-user state
**Clean.** Module level holds only read-only constants:
- `_BUSY_REASONS`, `POSE_DOWNLOAD_FORMATS`, `OTHER_DOWNLOAD_SUFFIXES`, `PROVIDER_LABELS`, `SCOPE_LABELS`, the guard script;
- `ui_nice/app.py`'s `WORKER_CAP` / `OUTPUT_BASE` config.

Settings Apply writes only the client's own `GuiSession` (`use_settings`) and the shared settings file, as Streamlit does.

### (c) The `/files` route
- **Clean.** `resolve_job_file` holds against everything tried:
  - `..`, `%2e%2e`, absolute paths;
  - a symlink pointing out, a NUL byte;
  - `saved_results/`, `uploads/`, a directory;
  - an id that isn't a plain `job_…` name.
- **Hardening (Quality #4):** `_JOB_ID` used `match` with `$`, which also accepts a trailing newline. That was harmless, since no such directory can exist, but it is now `fullmatch`, with a test case.
- `saved_results/` is excluded only to avoid listing a bundle's copies twice. It is not a security boundary, so its case on a case-insensitive mount doesn't matter.
- The route is reachable only through `127.0.0.1:3001`. In the real-data check, five bad paths gave 404 (among them `%0A` after the id) and real downloads gave 200.

### (d) Write paths, Linux named volume
- **Setup:** root, in the `ui` image, made two sessions:
  - `WorkflowStateManager`, a checkpoint and `workflow_results.json`;
  - a plain-`mkdir` 0o755 `docking_results/` (the worst case);
  - `rename_job`, which leaves a root-owned lock file;
  - a settings save.
- **Then uid 1000, in the `ui-nice` image:**
  - **OK:** Rename (`rename_session_file`), a new checkpoint, and the settings save over root's file once `cache/` belongs to uid 1000.
  - **Failed:** Delete stopped at the root-made 0o755 subdir with "Permission denied", after removing part of the folder.
- **Accepted as a documented risk:**
  - Every directory the current code makes inside a job dir is shared (`ensure_shared_directory`; bundle dirs since R2). Only data made before 16.3, or by an older root GUI, has such a subdir.
  - `docs/DOCKER_DEPLOYMENT.md` already lists "deleting a job dir the root GUI created" under the one-time `chown`.
  - The failure is reported ("Delete failed: …"), never silent. Streamlit goes in 16.8.
- The volume was removed.

### (e) Busy and running rules
Rename was the one gap (Correctness #4). Load and Restore stay off while this client runs; Delete is off only for the running row, as designed. Delete re-checks the running job and `busy` after the dialog.

---

## Quality (`/simplify`)

### Fixed
1. **One reset for a session switch** (altitude #1). `form.detach_run()` drops the bridge, the run log and a staged redock. `start_new_session` and `resume_session` both call it, rather than keeping two lists of what to clear. Those two lists had drifted, which is how 16.7's Load kept the bridge.
2. **The session-name limit is one constant** (simplification #2, reuse #1). `ui.job_history.JOB_NAME_MAX_CHARS` serves `rename_job`, `apply_session_rename`, `run_docking` and both NiceGUI name fields. The 60 had five copies; Streamlit's two `max_chars=60` stay until 16.8 removes `ui/app.py`.
3. **The Sessions tab does nothing while hidden** (efficiency #1). Its per-row setters ran on every tick of a run for a tab nobody was looking at, and all sessions are listed now. `set_shown(True)` syncs. The unused `_Row.job` field went (simplification #5).
4. **`_JOB_ID` uses `fullmatch`** (see (c)).

Efficiency #2 (rows rebuilt on every refresh) is fixed by Correctness #2.

### Deferred, with triggers (all three parts in place)
1. **Load and the Save button's check read files on the event loop** (`/code-review`). About 55 ms for the largest (1.2 MB) session on the bind mount.
   - **Recommendation:** leave it, under the owner's 2026-10-12 decision for R2 Deferred #1. It is the same work, and the GUI is single-user on `127.0.0.1`.
   - **Trigger:** R2's comment in `ui/core/run_control.py::run_docking` is widened to name both. Future Work's R2 Deferred #1 bullet says so.
2. **A pose click re-sends every ligand's pose to the viewer** (efficiency #4). `set_ligands` sends the whole dict, a few KB per ligand, so about 1 MB per click on a 300-ligand batch.
   - **Recommendation:** leave it until a click on a large batch is noticeably slow. Batches that size are rare, and the fix needs a new `MolViewer.set_ligand` and JS method.
   - **Trigger:** a comment on `ResultsView._redraw_poses`, plus a Future Work bullet.

### Declined, with reasons
- **A `KeyedRead` helper for the four pending-read patterns** (simplification #1). Correctness #6–#8 sat in exactly these, so the idea is tempting. But they differ in what makes a result stale (a load key, a generation number, an endpoint), and each fix was one boundary catch. A helper would add indirection for three call sites that 16.8 doesn't change.
- **One `bridge_outcome()` for "how did the run end"** (simplification #7, reuse #4). The callers mean different things on purpose: the controller doesn't switch tabs for `cancelled` (owner's 16.6 choice), while Results draws it as failed.
- **One endpoint field instead of two** (simplification #4). The Ollama endpoint lives under Advanced, as in Streamlit. One element moved between containers would be more code than the copy.
- **`kind="failed"` in `_Shown`** (simplification #6). It reads slightly cleaner but changes nothing.
- **The spinner shown twice** (simplification #8). Harmless. `_begin_refresh` shows it for callers that don't sync straight after.
- **Streamlit-only cleanups** (simplification #3, #9, #10, reuse #5): the inline log tail, the `_PROVIDER_LABELS` alias, the receptor read. `ui/app.py` is deleted in 16.8.
- **A public `job.log` name or `dock_results()` helper** (reuse #2, #3). One-line lookups of a fixed on-disk contract.
- **`busy_with()` and an `allow_running_other` argument on the controller** (altitude #2's broader half). Two background kinds and two views use them. The Rename gap is fixed and tested; one more view would be the time to centralise it.
- **`job_comparator` keyed by job ID** (altitude #3). The agent recommended leaving it too: labels can still collide only for a user-typed "X (job_…)".
- **`state.job_done` in the Results load key when there is no bridge** (efficiency #3). Unreachable: Load is off while a run goes, and the viewed session is always the active one.
- **Reading only the appended part of `job.log` during a run** (`/code-review`). The largest `job.log` across all 181 sessions on disk is 7 KB.

---

## Exception-handling pass

Every `try/except` the range added:
- **Kept (boundary catches):**
  - `resolve_job_file`'s resolve (a symlink loop, a NUL byte) and `job_file_url`'s `relative_to`;
  - `job_log_if_changed`'s `FileNotFoundError` (no log yet);
  - the Compare and Apply catches, which show any failure, as Streamlit did;
  - `_on_load`'s `OSError` / `ValueError`, around the user's action;
  - `remove_session_dir`'s `OSError` and `rename_session_file`'s catch, which both return the message.
- **Widened to the task's top, each reporting the failure** (Correctness #6–#8): the three background reads.
- **Added** (Correctness #9): `poll()`'s `OSError`, an optional display read.

No band-aids found.

---

## Rules §5: Cross-Service Compatibility Review

- **MCP tool signatures:** the range changes no `services/*/server.py`. `compare_jobs` keeps its return shape, and its only change is the label text. Its three callers all read `jobs` / `affinities` / `best_per_ligand` as before: `nlp_orchestrator.py`'s ASCII table, Streamlit, and NiceGUI. **Clean.**
- **`docker-compose.yml`:**
  - `ui-nice`'s block matches `ui` line for line except the build, image, name, port (`127.0.0.1:3001`) and health URL (`/health`).
  - Every `MCP_*_URL` / `PYMOL_*` points at the service's container name and the port its `server.py` fixes (8001–8005, 8090).
  - `OUTPUT_BASE` and the settings path match the mounts.

  **Clean.**
- **Security boundary:** every published port is on `127.0.0.1`: 3000, 3001, 6080, 8001–8005, 8090 and 11434. `docs/DOCKER_DEPLOYMENT.md` matches. **Clean.**
- **Image rebuild finding:**
  - `Dockerfile.ui_nice`'s unpinned `pip install` now pulls Starlette 1.8.0.
  - `test_download_route_streams_a_large_file` then failed on `0fd1d9f` too ("Unexpected message received: http.request").
  - Its fake ASGI `receive()` answered every call with a request, while a server waits until the client disconnects, and Starlette 1.8's `FileResponse` now listens for that.
  - The route itself is fine: the live download gave 200. Only the test's fake changed.
  - Pinning is left to 16.8's Dockerfile cleanup, as Streamlit's pin was.
- **Fresh stack (2026-10-13), owner-approved:**
  - Built `mcp-docking-base`, then every image.
  - `docker compose -p af6ad48c… down` (no `-v`), then `up -d` from the repo's Windows path. The project name kept the Ollama volume (`gemma3:4b`).
  - All 9 containers healthy. `mcp-ui` and `mcp-ui-nice` now mount the Windows path, which ends their restart loop since the Docker restart (they had been created from the WSL side).
- **Checks on the stack's own network** (local Ollama, no paid key):
  - `nlp_orchestrator.py --check-health`: all healthy.
  - `tests/test_docking_stop_integration.py`: 1 passed.
  - **CLI workflow:** "Download protein 6LU7 and ligand ATP, prepare both, and dock ATP…" with box `[-10.8, 12.5, 68.9]`, 22 Å. Download → prepare → dock → visualize completed, `job_20261013_105940_802971`, best −3.9 kcal/mol.
  - **Observed, pre-existing, accepted:** the CLI creates the job directory before its "Execute this workflow?" prompt, so a declined or EOF'd prompt leaves a folder with a checkpoint and no results. No GUI lists it, so it is clutter only. Recommendation: leave it.
- **Real run driven from NiceGUI, headless:**
  - **Setup:**
    - a side container `ui-nice-check-r3`: `compose run` of `ui-nice` under the live project, so it has the same mounts, env and network;
    - local Ollama `gemma3:4b` and a throwaway settings path;
    - Playwright 1.55 with `r3check_*` copies of the receptor and the crystal ligand.
  - **What passed:**
    - Local Files, the ligand dropped twice: one listed, with "(replaced the earlier r3check_ligand.mol2)".
    - The box on the ligand's centre `[15.83, 14.45, 3.86]`, 20 Å, and Run Docking. The run finished in 7–9 s and Results opened by itself.
    - The chart was drawn, and a pose-row click showed "Mode 2".
    - 14 download links: three fetched (200), five bad paths (404).
    - The tab moves above gave 0 Plotly errors, with the guard installed.
    - `save results as r3check_bundle` from the chat.
    - Rename to `r3check_renamed`: the row showed it at once, and the Results status followed.
    - Load opened Results with "Viewing session r3check_renamed".
    - Delete through the dialog.
    - No page or console errors.
  - **On the way:**
    - Correctness #3 found and fixed. The side container was restarted on the fix and the driver re-run at full speed.
    - Driver fixes, none in the app: strict-mode selectors (twice), markdown-rendered backticks, and a row locator that matched the chat.
  - **Cleaned up:** the side container, the 6 job dirs (the CLI's two and the driver's runs not deleted through the UI) and the `r3check` upload dirs. `orchestrator_output/` is back to 194 entries and `uploads/` to 21.
- **Live `mcp-ui` and `mcp-ui-nice` restarted** on the final tree: both healthy, NiceGUI's `/` 200, a live `/files` traversal 404, Streamlit's health 200, the guard in the page.

---

## Verification

- **Suites, on the final tree:**
  - Hermetic (`script-runner`): 1435 passed, 27 skipped. 16.7: 1430 / 27, plus its Load fix's test.
  - `ui` image whole suite (`-m "not integration"`): 1598 passed, 19 skipped, 2 deselected.
  - `ui-nice` tier: 299 passed. 16.7: 290.
- **Fail-before:** each regression test was run in a temporary worktree on `0fd1d9f`, the code before the fixes, to show it catches its bug. All 11 failed there:
  - 10 NiceGUI tests: 9 new, and `test_load_delete_and_restore_wait_for_a_save`, extended with the Rename checks;
  - the hermetic upload test.

  Four of them also reported teardown errors: the uncaught background-task exceptions that Correctness #6–#9 fix. The rename-race test (#3) was checked the same way in a later run. The worktree was removed.
- Linux named-volume permission check: focus (d).
- Fresh stack, CLI workflow and the NiceGUI-driven run: §5 above.
