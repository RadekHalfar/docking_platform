# Review Stage R2 — Phase 16 (Stages 16.3–16.5)

**Scope:** `11b5b52..3bb9cdb`, everything since Phase 16's Review Stage R1
fix commit:
- `f923690`: Stage 16.3, the `ui-nice` service, the page layout, the test harness and the parity table.
- `48190bb`: the greeting's "form on the left" wording.
- `e64b136`: Stage 16.4, the Setup tab, the chat drawer, the run-owned log and the stale-parse guard.
- `3bb9cdb`: Stage 16.5, the 3Dmol viewer element and the box picker, including the upload `Errno 13` fix.

Nothing else landed after R1, so there is no review gap.

The plan asked this review to check three things about the new NiceGUI code:
- (a) **the event loop is never blocked** (finding 7);
- (b) **no per-user state lives at module level** (finding 8);
- (c) **the non-root write paths work** (finding 5).

All browser clients share one NiceGUI process and one asyncio event loop, so anything one tab blocks, every tab waits for.

**Rules §5 (cross-service): light check only.** The range adds the `ui-nice` compose service and build-script entries. The full Cross-Service Compatibility Review is scheduled for R3, after 16.6–16.7. Checked here:
- `ui-nice` binds `127.0.0.1:3001` only;
- it copies `ui`'s mounts (read-only source, rw `cache/` and `orchestrator_output/`) and `ui`'s MCP environment;
- its healthcheck hits `/health`.

No MCP tool signature or result shape changes. The only `common/` changes are `job_log.py`'s file mode (16.3) and this review's `ensure_shared_directory` docstring.

**Method** (`development_process_rules.md` §4):
- `/code-review` (correctness, high effort) over the range.
- `/simplify`, run report-only, with four parallel angle agents: reuse, simplification, efficiency and altitude.
- **(a) Event loop.** I traced every NiceGUI handler and the `tick()` timer down into `ui/core` and listed each synchronous file read or write. The expensive ones were then timed on real data in the `ui-nice` image, on the Docker Desktop bind mount the live stack uses.
- **(b) Per-user state.** I grepped `ui_nice/`, `ui/core/` and the modules they import for module-level mutable objects, `global`, caches and `app.storage`, then followed each module-level object that reaches a client's state.
- **(c) Write paths.** I re-audited every path the UI writes, then re-ran 16.3's Linux named-volume check on this tree: root (`ui` image) writes first, then uid 1000 (`ui-nice` image). This time it includes overwriting a root-saved bundle and saving settings over root's file.
- A dedicated exception-handling pass.
- **A real-data run on a side `ui-nice` container** (this tree, the live services), with two browser clients at once.

Every candidate was checked against the real code before triage. Every new
regression test was run against `3bb9cdb`'s production code in a temporary
worktree (see Verification).

**Two rules restated, not just pointed at:**
- Exceptions are never used as a band-aid. A broad catch belongs only at a real I/O/protocol boundary, and it must turn the failure into an explicit failed state, never make it vanish.
- No finding is manufactured to look thorough. Candidates not worth acting on are reported as declined, with the reason. Focus (b) found nothing beyond one hardening, and it is reported that way.

**Owner decisions during the review (2026-10-12):**
- **Uploads go into a directory of their own each** (Correctness #4), in `ui/core` for both GUIs.
- **A saved bundle's own directories are made writable by every uid** (Correctness #3), rather than running Streamlit as uid 1000 until 16.8 or accepting the risk.
- **NiceGUI ligand drops add up**, with Clear to empty them (Correctness #6), as Streamlit's uploader does.
- **Run / Confirm redock / Save Session's file work stays on the loop for now** (Deferred #1), with a trigger.

**Outcome:**
- **The three focus questions:**
  - **(a) Not held, now fixed where it matters.** These blocked every client:
    - `save results as`: 34–37 s for a real 17 MB session;
    - the geometry command: 35–80 ms;
    - the timer's progress read and the box picker's `exists()`: ~10 ms and ~3.4 ms per client, every second and on every keystroke.

    All three now run in `run.io_bound` (Focus (a) #1–#3). Run, Confirm and Save Session still block for 150–600 ms per click: Deferred #1.
  - **(b) Clean.** The only module-level values are constants and config. `WORKER_CAP` and `OUTPUT_BASE` are read once from compose/env; the greeting dict is copied per client. One module-level list (`PREP_SERVICES`) was stored by reference in each client's parser and never mutated. It is now copied anyway (Quality #6).
  - **(c) Mostly held.** Two gaps were fixed:
    - a bundle the other uid saved couldn't be overwritten (#3);
    - a same-named upload replaced an earlier session's file (#4).

    Settings work as designed once `cache/` is host-owned, which the documented `chown` covers.
- **Correctness:** 7 findings, all fixed. One (#4) is pre-existing in Streamlit; the others came in with 16.3–16.5.
- **Focus (a):** 3 fixed, 2 deferred with triggers, 1 deferred forward note (#3) for 16.7.
- **Quality:** 8 fixed, 12 declined with reasons.
- **Exception pass:** clean.

---

## Correctness (`/code-review`)

### 1. A run that ended before the first timer tick was never drawn (16.4)
- **Where:** `ui_nice/controller.py::tick`.
- **Symptom:** `tick()` redrew only if a bridge had been *running* at that tick or the one before. Suppose Run Docking starts a job that fails or finishes within the 1 s interval (bad input, an immediate service error). The next tick processes it: it posts the reply, fills the run log and sets `job_done`. But the page is never synced, so the reply stays hidden until some other action. 16.5's Changelog fixed the same gap for the prep bridge with a special case (`prep_pending`).
- **Fix (also the altitude finding on `tick`):** the functions that change the state now report it. `run_control.process_docking_bridge` and `box_picker.process_box_picker_bridge` return whether they changed the state (new log lines, an outcome posted, a prep handled). The tick redraws when that is true or while a job runs. This replaces both `_poll_once_more` and `prep_pending`.
- **Tests:**
  - `test_a_run_that_ends_before_the_first_tick_is_drawn` (`tests/test_nicegui_setup.py`);
  - the return values: `test_process_docking_bridge_reports_whether_the_state_changed` and `test_process_box_picker_bridge_reports_a_handled_prep`.

### 2. An unreadable receptor was re-read forever, with nothing shown (16.5)
- **Where:** `ui_nice/box_picker_panel.py::_load`.
- **Symptom:** a receptor that exists but raises `OSError` on read (another uid's 0o600 file, a directory, EIO) made `_load` reset `_loading` and call `sync()`. `path.exists()` was still true, so `sync()` started the same read again at once. That is an endless loop on the event loop and the thread pool, with a blank viewer and no message.
- **Fix:** a failed read is recorded per receptor (`_failed`), and `sync()` doesn't retry it. The panel shows "Prepared receptor no longer on disk" (not found) or "Could not read the prepared receptor (…)" (anything else), with Reload. `sync()` no longer touches the disk at all (Focus (a) #3).
- **Test:** `test_an_unreadable_receptor_is_read_once_and_reported`. On `3bb9cdb` the read repeats, and the message never shows.

### 3. A bundle the other GUI's uid saved could not be overwritten (16.3; owner's choice of fix)
- **Where:** `services/llm_orchestrator/interactive_session.py::save_results_bundle`.
- **Symptom:** root Streamlit saves bundle `final`, and `copytree` leaves its subdirectories 0o755 root-owned. NiceGUI (uid 1000) then overwrites `final`:
  - the rename to `.final.replaced` succeeds;
  - `rmtree(..., ignore_errors=True)` silently fails on the root-owned contents;
  - every later overwrite of that name then fails, because `.final.replaced` is still there.
- **Fix:** after writing a bundle, each of its directories gets `ensure_shared_directory()` (0o777), so either uid can replace it. That applies to bundles saved from now on. Older root-saved bundles need the documented one-time `chown`.
- **Tests:**
  - `test_bundle_directories_are_writable_by_every_uid` (`tests/test_save_bundle.py`);
  - the Linux named-volume check (Verification): uid 1000 overwrote root's bundle twice and left no leftovers.

### 4. A same-named upload replaced the file an earlier session docks (pre-existing in Streamlit; owner's choice of fix)
- **Where:** `ui/core/uploads.py::save_upload`.
- **Symptom:** every upload, from every tab and both GUIs, went to one flat `uploads/<basename>`. Client A uploads `ligand.sdf`. Client B uploads a different `ligand.sdf`, and `os.replace` swaps it in. A's next Run (or a redock of A's session much later) docks B's molecule, with no warning. NiceGUI's one process for many tabs makes this easier to hit.
- **Fix:** each upload goes into a new directory of its own, `uploads/upload_<random>/<basename>` (`tempfile.mkdtemp`, then made shared). No upload is ever written over. 16.5's tmp-file-and-replace dance is no longer needed. A failed write removes its directory.
- **Streamlit:** its upload callbacks re-save every file on each `on_change`, and Streamlit can re-fire the callback for the same files. On such a re-fire the wrapper now keeps the copies already saved, so the parameters don't change and no needless ligand prep is planned.
- **Tests:**
  - `test_a_same_named_upload_leaves_the_earlier_file_alone` (`tests/test_ui_core_uploads.py`);
  - the three existing tests that pinned the flat path or the in-place replace were rewritten to the new layout.

### 5. The stale-parse guard missed a Load of a session without saved parameters (16.4)
- **Where:** `ui/core/chat.py::parse_context`.
- **Symptom:** the guard compared the parser's workflow and the interactive session by identity. A Load re-activates the *same* session object. If the loaded job has no draft or checkpoint parameters, the Load also keeps the parser's workflow. An LLM answer for the previous session was then applied to the newly loaded one. Load arrives in NiceGUI in 16.7.
- **Fix:** the context also holds `state.job_id`, compared by value.
- **Test:** `test_parse_dropped_after_loading_a_session_without_saved_parameters` (`tests/test_ui_core_chat.py`).

### 6. A second ligand drop replaced the first (16.4 parity regression; owner's choice of fix)
- **Where:** `ui_nice/setup_tab.py::_on_ligand_upload`, `ui/core/uploads.py::apply_ligand_uploads`.
- **Symptom:** Streamlit's multi-file uploader keeps every file it has been given. NiceGUI's resets after each drop, and the handler set `ligand_files` to that drop only. Dropping `a.sdf` and `b.sdf`, then `c.sdf`, docked one ligand.
- **Fix:** `apply_ligand_uploads(..., append=True)` adds the drop to the files already set, and the reply names them all. NiceGUI passes it; **Clear** empties the list. The upload label says so.
- **Tests:** `test_a_second_ligand_drop_adds_to_the_list` (NiceGUI) and `test_appended_ligand_uploads_add_to_the_list_and_name_them_all` (core).

### 7. Reset and New Session left the box picker's Load message up (16.5)
- **Where:** `ui_nice/box_picker_panel.py` (`load_message` lived on the view).
- **Symptom:** a Load that couldn't start ("Provide a protein source first") stayed under the button through a Reset, into the fresh session.
- **Fix:** the message moved to `PageController.picker_message`, which `start_action()` clears like the action error. That matches Streamlit, where any rerun drops it. Load and Discard call `start_action()` too.
- **Test:** `test_a_load_message_is_cleared_by_reset`.

---

## Focus (a): the event loop

Measured in the `ui-nice` image on the live stack's Docker Desktop bind
mount (scratchpad scripts; the data was read, never changed).

### Fixed
1. **`save results as` copied the bundle on the event loop** (efficiency angle).
   - **Cost:** 34.2 s and 37.4 s for two real sessions with 17 MB of docking results each. The same copy takes 81–157 ms on tmpfs, so the bind mount's per-file cost dominates. Every client froze for that time.
   - **Fix:** `ui/core/save_results.py` now saves in three steps:
     - `plan_save()`: the checks, on the loop;
     - `write_bundle()`: the copy, touching no GUI state;
     - `finish_save()`: the reply and the name clash, on the loop.

     `save_results()` still does all three inline for Streamlit. `chat.submit(..., run_save=)` hands the command to `PageController.run_save`, which runs `write_bundle` in `run.io_bound`. Meanwhile `controller.busy` holds the session: the chat input shows "Saving results...", and Run, New Session and Reset are off.
   - **Test:** `test_save_copies_off_the_event_loop_and_holds_the_session_meanwhile`. It asserts the copy ran off the loop, and that the controls were off while it ran.
   - **Real data:** client B's chat reply came back in 42 ms while A's save was still running (Verification).
2. **The geometry command read and parsed the receptor on the loop.**
   - **Cost:** 34–77 ms for 0.4–0.9 MB receptors; more for a large protein.
   - **Fix:** `box_picker.read_protein_geometry()` (file read, no state) and `set_protein_geometry()` split the old `apply_protein_geometry()`, which Streamlit keeps using. The immediate path (`chat.submit(..., apply_geometry=)`) and the deferred path (`process_box_picker_bridge(..., apply_geometry=)`) both take NiceGUI's `PageController.apply_geometry`. It reads in `run.io_bound` under a lock, so queued commands apply in order.
   - **Test:** `test_a_geometry_command_reads_the_receptor_off_the_event_loop`. The deferred-path test now waits for the reply.
3. **Disk reads in every `sync()`.**
   - **Cost:** `_sync_run_card` read `docking_progress.json` (median 8.8–10.5 ms) and `BoxPickerView.sync` called `path.exists()` (median 3.4 ms). Every sync did this: each timer tick per client, but also every keystroke in a Docking Box field.
   - **Fix:**
     - Views now have an async `poll()` that the timer awaits before a sync; `SetupView.poll` reads the progress in `run.io_bound`.
     - `sync()` reads what `poll()` cached, keyed by job id so a new run never shows the last one's numbers.
     - The box picker learns whether its receptor exists from its one read (#2 above).
     - Also (efficiency): the "Currently docking" labels are kept and retexted instead of deleted and rebuilt every second.
   - **Test:** `test_progress_is_read_off_the_event_loop`.

### Deferred, with triggers (all three parts in place)
1. **Run Docking, Confirm redock and Save Session do their file work on the loop.**
   - **Cost:** 146–602 ms per click: validation, `WorkflowStateManager` init, checkpoint, `job.log`. `undo` and `show history` from chat are the same kind, smaller.
   - **Fix:** split each like `save_results` (plan → `run.io_bound` → apply), with `controller.busy` holding the session.
   - **Recommendation (owner's decision):** leave it for now. The deployment is single-user on `127.0.0.1`, so only that user's other tabs stall, once per click. Streamlit blocked that user the same way. These handlers also write the client's state while its timer reads it, so splitting them is a bigger change than the save.
   - **Trigger:** comment in `ui/core/run_control.py::run_docking`. Fix if the GUI serves more than one user, or a stall is noticed. There is a matching Future Work bullet.
2. **The Save-as conflict buttons (Overwrite / Save as `<name>_HHMMSS`) still copy inline.**
   - **Status:** `resolve_save_conflict` → `save_results`. Nothing in NiceGUI calls it yet; 16.6 wires it.
   - **Fix:** run it as `PageController.run_save` does.
   - **Recommendation:** do it in 16.6, with the panel.
   - **Trigger:** comment in `ui/core/save_results.py::resolve_save_conflict`, plus a Future Work bullet.
3. **16.7's Load, Delete and revision restore must respect `controller.busy`.**
   - **Risk:** a Delete during a save removes the job dir under the copy; a Load swaps the session the save reports to.
   - **Recommendation:** add the check with those actions, as `SetupView._blocked()` does.
   - **Trigger:** comment on `PageController.busy`, plus a Future Work bullet.

### Declined, with reasons
- **`load_settings()` and `build_llm_client()` per page open** (`ui_nice/app.py::index`). One small JSON read, once per page load. Building the client opens no connection.
- **The bridge finish in `tick()`.** It sets `session.last_results` from the bridge's in-memory result and reads no file.
- **The box redraw per keystroke in the browser** (efficiency #3). This is browser work, not the server's loop. 16.5 measured a box-only redraw at a median of 143 ms, and the owner's check of typed values passed. 16.8 re-times the GUI anyway.

---

## Quality (`/simplify` + 2 from `/code-review`)

### Fixed
1. **A page sync started nested syncs** (`/code-review`; altitude #3). NiceGUI fires a widget's change handler when `sync()` writes it. Each Docking Box field's handler then ran a full `controller.sync()`, one level deeper per field, up to six. Each level also read the progress file while a run was going. Now `PageController.sync()` sets `syncing`, and the Setup handlers return at once while it is set. The `_on_algorithm` comment that worked around a double reset is gone.
   - **Test:** `test_a_sync_does_not_start_another_sync` (7 calls on `3bb9cdb`, 1 now).
2. **`ensure_shared_directory`'s docstring said checkpoints and bundles don't need it** (`/code-review`; altitude #1), the opposite of what 16.3 made them do. It now names every directory the two GUIs share.
3. **The two upload handlers were one handler twice** (simplification #4; efficiency #9). There is now one `_save_files()` helper, and a drop's files are saved in one `run.io_bound` trip, not one per file. One `_ligand_upload()` builder replaces two copies of the ligand `ui.upload`, and `LIGAND_ACCEPT` is a constant.
4. **The picker's placeholder size was spelled out in four places** (reuse #5; simplification #2). It is now `box_picker.viewer_box_size()` in both NiceGUI uses. Streamlit's copy is left; it goes in 16.8.
5. **"Sorry, I couldn't parse that" was written twice** (reuse #7). It is now `chat.parse_failed()`, used by both `answer_with_llm` and the chat drawer.
6. **`PREP_SERVICES` went by reference into each client's parser** (focus (b)). It was never mutated (`chat.parse_message` builds a new list), but one in-place change would have leaked across clients. Each prep now gets a copy.
   - **Test:** `test_prep_gives_each_client_its_own_services_list`.
7. **The tick's redraw rule** (altitude #2) became "changed or running". See Correctness #1.
8. **The "Currently docking" labels** are kept and retexted (efficiency #4). See Focus (a) #3.

### Declined, with reasons
- **`PageParts` has fields nothing reads yet** (simplification #1). They are the hand-over to 16.6–16.7, which fill those regions (`layout.py` says so). Trimming them now would add them back next week.
- **`_shown` / `_loading` in the box picker** (simplification #3). They are different things: the receptor drawn, and one being read. A single field would lose the "a newer receptor arrived during the read" check.
- **CPU and Workers outside the `_fields` registry** (simplification #5). Both need number coercion and clamping that the text-field loop doesn't do.
- **`form.max_workers()` is a getter that clamps the stored value** (simplification #6; altitude #4). Both GUIs draw the field after every change, so the stored value is clamped before any Run. Moving the clamp into every setter is a cross-GUI change for no user-visible gain. Revisit when Streamlit is gone (16.8).
- **The three button/tooltip blocks** (simplification #7). This is three explicit cases, and a table wouldn't be shorter.
- **`MolViewer` keeps attributes beside `_props`** (simplification #8). The tests read what was sent from those attributes (16.5's harness design), and `set_ligands` / view mode are 16.6's.
- **`announce=` and the clear-upload wrappers** (simplification #9). They exist for Streamlit's re-fired callbacks and go with it in 16.8.
- **The `View` protocol** (simplification #10). It now also documents `poll()`.
- **Scenario builders duplicated between `test_ui_apptest.py` and the NiceGUI harness** (reuse #1), **the shared Streamlit/NiceGUI UI strings** (reuse #4, #6), and **`OUTPUT_BASE` / `UPLOAD_DIR` defaults in both apps** (reuse, minor). The Streamlit copies are deleted in 16.8, and moving the strings to `ui/core` now would only churn `ui/app.py` for six weeks.
- **`RunningBridge` copies in the older core tests, and the Streamlit-wrapper upload test** (reuse #2, #3). These are test-only files that predate the range, or exist to cover Streamlit wrappers that go in 16.8.
- **Per-site permission fixes vs. one uid for both GUIs** (altitude #1). The owner chose the shared bundle tree. The uid split ends with Streamlit in 16.8, and the services already write as uid 1000, so the shared-directory calls stay useful after it.
- **Markdown in replies is not escaped** (altitude #6). `code-friendly` covers the underscores that actually broke. A `*` or backtick in a file name would still format in both GUIs; that is cosmetic and pre-existing.

---

## Exception-handling pass

Clean. Every `try` in the range's code is a boundary catch:
- **Best-effort `chmod`s** in `job_log.append_job_log` and `ensure_shared_directory`, for filesystems without POSIX bits.
- **Turned into a visible state:**
  - `load_settings`' corrupt-file fallback (pre-existing);
  - the upload handlers' `OSError`/`ValueError` → the action error;
  - the box picker's read failures → the panel message (Correctness #2);
  - the geometry read's `OSError`/`ValueError` → the chat reply.
- **The chat drawer's broad catch around the LLM parse**, the same boundary as `answer_with_llm`.
- **New: `PageController._save`'s broad catch.** `save_results_bundle` already turns file errors into a result, so this catches anything else in the background task (e.g. unreadable checkpoints). It logs the traceback and replies "Save failed: …". Without it the user would get no reply, only a dead spinner.
- **`save_upload`'s `except BaseException`** removes the half-written upload's directory and re-raises.
- **`mol_viewer.js`'s `?perf` URL probe** turns the flag off if the URL can't be read.

---

## Verification

- **Regression tests fail before, pass after.**
  - **Method:** the nine changed test files ran against `3bb9cdb`'s code in a temporary worktree (`git worktree add --detach`), removed afterwards. The harness's `busy` wait was dropped there, since that attribute doesn't exist on the old code.
  - **All 8 new NiceGUI tests fail on the old code**, each with its own symptom:
    - the run's reply is never drawn;
    - the progress, geometry and save reads run on the loop;
    - 7 nested syncs;
    - the second drop replaces the first;
    - the unreadable receptor is read again and again;
    - the Load message survives Reset.
  - **10 core tests fail on the old code:**
    - the stale parse, the bundle dirs (0o755) and the same-named upload fail on behaviour;
    - the others fail because the new API is missing there: the return values, `read_protein_geometry`, `append=`, and the upload layout.
- **Suites:**
  - **Hermetic:** `docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/` gave 1398 passed, 22 skipped (16.5: 1387 / 24).
  - **`ui` image, whole suite:** `docker compose run --rm --no-deps ui python -m pytest -m "not integration"` gave 1561 passed, 16 skipped, 2 deselected (16.5: 1550 / 18). One Streamlit helper test pinned the flat upload path and was updated.
  - **`ui-nice` tier:** `docker compose run --rm --no-deps ui-nice python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py` gave 222 passed (16.5: 214).
- **Linux named-volume permission check (focus (c)).**
  - **As root in the `ui` image:**
    - a job dir and a parameters checkpoint;
    - `docking_results/` made with a plain `mkdir` (the worst case);
    - an upload `ligand.sdf`;
    - bundle `b1`;
    - a settings save.
  - **Then as uid 1000 in the `ui-nice` image**, all OK:
    - a checkpoint;
    - `ligand.sdf` uploaded again (it lands in a second directory, and root's file is untouched);
    - root's `b1` overwritten, and overwritten again, with no `.b1.*` leftovers;
    - a new bundle `b2`.
  - **The settings save over root's file** failed while the check's own `cache/` was a root-made 0o755 directory. It passed once `cache/` was owned by uid 1000, as a host-created, `chown`ed `cache/` is. Replacing root's 0o644 file then works, which is what 16.3's `atomic_write_json` change was for.
  - The volume was removed afterwards.
- **Real data, headless (2026-10-12).**
  - **Setup:**
    - A side container `ui-nice-check-r2` ran this tree on the live network (`--volumes-from mcp-ui-nice`).
    - LLM: local Ollama `gemma3:4b`, with a throwaway settings path; no paid key, and no LLM call was made.
    - Playwright 1.55 drove two browser contexts, A and B, at once, with copies of `receptor.pdb`, `crystal_ligand.mol2` and `ATP.sdf` renamed `r2check_*`.
  - **A:**
    - Local Files; the protein, then the two ligands as two separate drops ("2 ligand file(s) ready": #6).
    - A box on the crystal ligand's centre, then Run Docking. `job_20261012_130848_ee121f` completed in about 15 s, and the Run log has "WORKFLOW COMPLETED SUCCESSFULLY".
    - `save results as r2check` took 1.1 s, during which "Saving results..." showed and the chat input and Run Docking were disabled.
    - The geometry command replied in 124 ms; Load receptor showed the viewer.
  - **B, at the same time:**
    - Chat replies came back in 133 ms idle, 48–61 ms during A's run, and 42 ms during A's save, with A still saving after B's reply.
    - B saw none of A's messages, and its Run log stayed hidden.
  - No console or page errors in either client.
  - **Two earlier attempts stopped on driver bugs, not app problems:**
    - a text selector matched both the caption and the summary;
    - markdown turns the job id's backticks into `<code>`.

    Their orphaned runs completed and were cleaned up with the rest.
  - **Cleaned up:**
    - the side container;
    - all four job dirs it created (two orphaned runs, the run, the prep);
    - the ten `upload_*` directories, each checked to hold only `r2check_*` files.

    `orchestrator_output/` and `uploads/` list exactly as before.
- **Live `mcp-ui` and `mcp-ui-nice` restarted** (`ui/core` and `ui/app.py` changed; 16.1's lesson). Both are healthy, and the headless smoke check passed:
  - Streamlit: `show history` replied, 25 Load buttons;
  - NiceGUI: "No active session — no history yet.";
  - no page errors in either.
