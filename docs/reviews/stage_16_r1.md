# Review Stage R1 — Phase 16 (Stages 16.0–16.2)

**Scope:** `7751701..2df69ec`, everything since Phase 15's Review Stage R1
fix commit:
- `cd1410e`: Stage 16.0, the Streamlit baseline timing and the NiceGUI spike findings.
- `41c6756`: Stage 16.1, `ui/core` part 1 (state, form, run control, sessions) and the bridge's stdout routing.
- `5cf06da`: Stage 16.2, `ui/core` part 2 (chat, box picker, save-as, results view).
- `2df69ec`: a test-only CI fix (two tests pinned to a CPU count) that landed after 16.2. No stage covered it, so it is reviewed here to leave no review gap (`development_process_rules.md` §4). It is clean.

Stages 16.1–16.2 moved the Streamlit page's controller logic into the
framework-free `ui/core/`, which both GUIs will now depend on. The plan
asked this review to confirm three things:
- (a) the move preserved behaviour;
- (b) no Streamlit or NiceGUI import leaked into `ui/core`;
- (c) the stdout routing is correct under concurrent runs.

**Rules §5 (cross-service) is not triggered by the range.** It changes no compose file, no `common/` module, no MCP signature and no result shape; the `services/` edits in the range are docstrings. This review's own change to `WorkflowOrchestrator` is checked under "Compatibility" below.

**Method** (`development_process_rules.md` §4):
- `/code-review` (correctness, high effort) over the range.
- `/simplify`, run report-only, with four parallel angle agents: reuse, simplification, efficiency and altitude.
- **Behaviour preservation (a).** A script extracted every moved function's old body (from `7751701`'s `ui/app.py` and `ui/session_state.py`) and its `ui/core` successor. It normalised the mechanical renames (`st.session_state[...]` → `state.<field>`, `st.rerun()` → `Outcome`) and diffed each of the 42 pairs. I read every remaining delta. The logic that used to live inside render functions (rename, delete, stop) was compared by hand.
- **Import leak (b).** Every `ui.core.*` module was imported at runtime in the script-runner image (no streamlit installed), then `sys.modules` was checked.
- **Stdout routing (c).** The proxy was read and probed. The altitude pass then questioned the mechanism itself (see Quality #1).
- **16.0 tooling.** `benchmark_gui*.py`, the `?perf` code in the viewer, and the `docs/GUI_GUIDE.md` baseline table against `docs/benchmarks/2026-10-07_*.json`.
- A dedicated exception-handling pass.
- A real-data run on a side Streamlit container (this tree, the live services).

Every candidate was checked against the real code before triage. Every new
regression test was run against `2df69ec`'s production code in a temporary
worktree (see Verification).

**Two rules restated, not just pointed at:**
- Exceptions are never used as a band-aid. A broad catch belongs only at a real I/O/protocol boundary, and it must turn the failure into an explicit failed state, never make it vanish.
- No finding is manufactured to look thorough. Candidates not worth acting on are reported as declined, with the reason. The efficiency angle found no new waste, and it is reported that way.

**Owner decisions during the review:**
- **Replace 16.1's stdout proxy with an `on_message` sink now** (Quality #1). Asked to choose between that and keeping the proxy with a one-line hardening, the owner chose the sink.
- **Fix the pre-existing double receptor prep now** (Correctness #1), rather than leave it for Stage 16.5.
- **Three declined findings become deferred with triggers** (2026-10-09, Quality deferred #4–#6). Each waits on a condition, and rules §4 requires a trigger for those.
- **Two more declined findings get fixed, and the CLI's history now reports the parameters revision** (2026-10-10, Quality fixed #7–#8). Their reason for declining ("removing them would add a behaviour diff") was weak on its own, and the second exposed a real CLI/GUI inconsistency.
- **Three more fixes, the fresh stack, and the changelog backfill** (2026-10-11). Asked whether anything worth fixing was left, the owner chose:
  - Correctness #4–#6;
  - the full fresh-stack rebuild left open since Phase 15 R1;
  - a `CHANGELOG.md` backfill for Phases 11–16, left open since 16.1.

**Outcome:**
- **The three focus questions:**
  - **(a) Behaviour preserved.** All 42 pairs differ only by the mechanical renames. Two places looked different and are not:
    - keys the old code `pop()`ped are now set to None, and the adapter's `drop_on_none` flag removes them;
    - the `st.rerun()` that left `_resume_session` is now in its `ui/app.py` wrapper.
  - **(b) No leak.** No module from streamlit, nicegui, `ui.app` or `ui.session_state` loads, even transitively; `ui/core` pulls in only `ui.job_history` and `ui.worker_cap` outside itself. The existing AST guard sees direct imports only, so a transitive guard was added.
  - **(c) Correct for the cases tested, but the wrong depth.** The proxy separated concurrent runs correctly, but `copy.copy(sys.stdout)` hit infinite recursion. It is now replaced by a per-run sink, so `sys.stdout` is never touched.
- **Correctness:** 6 findings, all fixed. Three were pre-existing, and one (#5) was introduced by this review and caught before commit.
- **Quality:** 8 fixed, 6 deferred with written triggers, 8 declined with reasons. The owner moved 5 findings out of "declined" on 2026-10-09/10: 3 became deferred, 2 were fixed.
- **Exception pass:** clean.
- **16.0 tooling:** clean. Every baseline number matches the JSON, and the `?perf` code only adds lines.

---

## Correctness (`/code-review`)

### 1. A geometry command during a running receptor prep started a second prep (pre-existing)
- **Where:** `ui/core/chat.py::protein_geometry_command` (before 16.2: `ui/app.py::_handle_protein_geometry_command`).
- **Symptom:** the user clicks "Load receptor for box picking", then types "set box center as protein center" before it finishes.
  - `start_box_picker_prep` cleared the receptor, so no receptor is found, and a second get_input_files + prepare_protein job starts.
  - It replaces `state.box_picker_bridge`, so the first job's result is never processed. The protein is prepared twice.
- **Pre-existing:** the code at `7751701` is identical. The owner chose to fix it now.
- **Fix:** if the box picker's prep bridge is running, record the pending target and reply "I'm still preparing the protein — I'll set this once it's ready." `process_box_picker_bridge` already applies a pending target when the job finishes.
- **Test:** `test_geometry_command_during_a_running_prep_waits_for_it` (`tests/test_ui_core_chat.py`). On `2df69ec` it starts a second prep.
- **Real data:** the side-container run hit exactly this path. The reply was "still preparing", then "Protein center set to (21.856, 17.732, 9.243)", with Center X = 21.856.

### 2. `_ThreadRoutingStdout` recursed forever on `copy.copy(sys.stdout)`
- **Where:** `ui/orchestrator_bridge.py`.
- **Symptom:** `__getattr__` read `self._fallback`. On an instance that `copy`/`pickle` create without `__init__`, that lookup recurses until `RecursionError`, which was confirmed in the image. The proxy was installed process-wide for the GUI's whole life, so any library that copies or introspects `sys.stdout` would hit it.
- **Fix:** the proxy is gone (Quality #1).
- **Test:** `test_runs_leave_sys_stdout_alone`. On `2df69ec`, `sys.stdout` is the proxy after a run.

### 3. `benchmark_gui.py --from-driver` lost every finished window when the driver's output ended badly
- **Where:** `benchmark_gui.py::sample_on_cue`.
- **Symptom:** the driver crashes after finishing `running-25`, a window that takes up to 30 minutes. `sample_on_cue` raises, `main()` never reaches `--json-out`, and the finished record is lost.
- **Fix:** the new `CueError(ValueError)` carries the finished records. `main()` saves them, then exits non-zero with the cue error.
- **Tests:**
  - `test_windows_finished_before_a_bad_cue_are_kept`
  - `test_main_saves_finished_windows_then_fails`
  - On `2df69ec` there is no `CueError`, and the run ends with a bare `ValueError` and nothing written.

#4–#6 were found in a last pass after triage, when the owner asked whether anything worth fixing was left. The owner chose to fix all three (2026-10-11).

### 4. A geometry command during a running prep could use another protein (pre-existing)
- **Where:** `ui/core/chat.py::protein_geometry_command`.
- **Symptom:**
  - A session with protein A is loaded. The form is switched to protein B and "Load receptor for box picking" is clicked.
  - Starting the prep clears the picker's receptor, so a geometry command fell back to the session's last prepared protein, A.
  - It computed the centre or box from the wrong protein, at once and without a warning.
- **Fix:** check for a running prep *before* looking for a receptor. While a prep runs, the command always waits for it.
- **Test:** `test_geometry_command_during_a_prep_ignores_the_sessions_old_receptor`. On `2df69ec`, A's centre is applied.

### 5. Several geometry commands sent during one prep kept only the last
- **Where:** `ui/core/chat.py`, `ui/core/box_picker.py::process_box_picker_bridge`, `GuiSession`.
- **Origin:** introduced by this review's Correctness #1, which made a second command wait while the pending target was still a single slot. It was caught before commit.
- **Symptom:** "center" and then "whole protein" sent during a prep were both told "I'll set this", but only the box size was applied.
- **Fix:** the field is now `pending_geometry_targets`, an ordered tuple with no duplicates (`_queue_geometry_target`). A finished prep applies each target in turn.
  - The adapter key is renamed with it (`_pending_geometry_targets`), and the existing tests follow it. The Streamlit AppTests changed only in that key and its value.
- **Tests:**
  - `test_geometry_commands_during_a_prep_are_all_queued`
  - `test_completed_prep_applies_every_queued_command_in_order`
  - Both fail on `2df69ec`.

### 6. A run's last log lines never reached the chat (pre-existing; was Deferred #1(a))
- **Where:** `ui/core/run_control.py::process_docking_bridge`.
- **Symptom:** the queue was drained only while the thread was alive. Lines printed just before it ended, such as "WORKFLOW COMPLETED SUCCESSFULLY", were lost on every Streamlit run.
- **Why fixed now:** this was the only deferral a user saw today. It had been deferred to 16.4, but fixing it doesn't conflict with 16.4's run-owned log.
- **Fix:** read `is_running()` first, then always drain, and stop there if the run is still going. Once the thread is seen finished, everything it printed is already queued, so the last drain can't miss lines.
- **Test:** `test_finished_bridge_streams_its_last_lines_before_the_reply`, which fails on `2df69ec`.
- **Real data:** in the fresh-stack run, the run's chat message contains "WORKFLOW COMPLETED SUCCESSFULLY" once.

---

## Quality (`/simplify` + 3 from `/code-review`)

### Fixed

1. **The orchestrator writes to a per-run sink; the global stdout proxy is gone** (altitude; owner decision).
   - At `VERBOSITY_LOW`, the bridge's level, the only output on the run path is `WorkflowOrchestrator._step_print`. The `MCP_DEMO` prints are CLI-only, and everything else goes through `logging`.
   - `WorkflowOrchestrator.__init__` takes `on_message: Callable[[str], None] = print`, so the CLI is unchanged. The bridge passes `_log_message`, which queues each line of a message. These are the lines `print()` + `_QueueWriter` gave, blank ones included.
   - Deleted: `_QueueWriter`, `_ThreadRoutingStdout`, the routing table and its lock, and the `contextlib`/`sys` imports. The bridge is about 70 lines shorter.
   - Process-global mutation can no longer reach NiceGUI's single process. A library `print()` on the bridge thread no longer lands in the user's chat.
2. **`summarize_sampler` reuses `ContainerStatsSampler.summary()`** instead of rebuilding it (`benchmark_gui.py`; `/code-review`).
3. **One `reply(state, content)`** in `ui/core/state.py`, replacing three identical `_reply` copies (`run_control`, `chat`, `box_picker`). NiceGUI gets one place to hook chat updates.
4. **`common.file_io.load_json_dict`** replaces two hand-written JSON readers: `sessions.load_workflow_results` and `results_view.read_docking_progress`. The contract is the same (None when the file is missing or unreadable), plus explicit utf-8 and a dict check.
5. **Dead code in `ui/app.py`:**
   - Four wrappers with no caller at all are deleted: `_sync_form_into_params`, `_handle_docking_algorithm_command`, `_handle_history_chat_intent`, `_handle_save_chat_intent`.
   - The unused `get_params` import is deleted.
   - The comment above the chat wrappers wrongly said the chat route goes through `_start_box_picker_prep`. It now says the two remaining geometry wrappers exist only for `tests/test_ui_app_helpers.py` (`/code-review`).
   - `docs/DOCKER_DEPLOYMENT.md`'s ui-tier list had been split by a paragraph inserted mid-list; the paragraph now follows the list (`/code-review`).
6. **A transitive import guard.** `test_importing_ui_core_loads_no_gui_framework_indirectly` imports every `ui.core` module in a subprocess and checks `sys.modules`. It also bites in the `ui` image, where streamlit is installed and the AST guard is the only other check.
7. **No more "is the session None?" guards** in `box_picker.find_receptor_pdb` and `chat.docking_algorithm_command` (simplification; first declined).
   - Only these 2 of the 13 places in `ui/core` that read `state.interactive_session` checked for None. `GuiSession` always creates a session, and Streamlit's `init()` sets one before any handler runs.
   - The guards could be reached in one place only: `tests/test_ui_app_helpers.py`'s autouse fixture cleared `st.session_state` without running `init()`. The fixture now seeds an `InteractiveSession` the way `init()` does.
8. **One shared helper for a revision's details and flags; the CLI reports the parameters revision** (reuse; first declined).
   - The new `describe_revision()` in `services/llm_orchestrator/workflow_state_manager.py` builds the "`source=initial_parse [params]`" text. Both `show history` tables now call it (`ui/core/sessions.py::format_history_markdown`, `nlp_orchestrator.py::_handle_show_history`); each keeps its own layout. The CLI's flags lose the second space before `[`.
   - **A real inconsistency, found because of the duplication (pre-existing):**
     - The CLI printed "Current revision: N" from `get_current_revision()`, which also counts `state_update` events.
     - The GUI printed the parameters' revision (`get_parameters_revision()`).
     - So after a run, the CLI named revision 2 while the parameters (what `undo` restores) were at 1.
   - **Owner decision:** the CLI now says "Parameters are currently at revision N.", as the GUI does. `docs/LLM_ORCHESTRATION.md`'s example is updated.
   - **Tests:**
     - `test_show_history_reports_the_parameters_revision_like_the_gui` (`tests/test_nlp_orchestrator.py`) fails on `2df69ec`, which prints "Current revision: 2".
     - `test_describe_revision` (5 cases) in `tests/test_workflow_state_manager.py`.

### Deferred, with triggers (all three parts in place)

Each one has a code comment naming the symptom, the fix and the trigger, plus a bullet in `dev_plan.md` Phase 16 Future Work.

1. **The run's log: wrong message, quadratic append → Stage 16.4.** Part (a) was fixed on 2026-10-11 (Correctness #6).
   - **Symptoms:**
     - (a) ~~Lines queued just before the thread exits are never drained~~. **Fixed:** the only one of these a Streamlit user saw on every run.
     - (b) Lines go to "the last message", so a chat message sent mid-run gets the log appended to it.
     - (c) `+=` rebuilds an ever-growing string each tick.
   - **Fix:** give the run its own log, set by `run_docking`/`confirm_redock`, and render it next to the chat.
   - **Recommendation:** worth doing. It is deferred only because 16.4 rewrites exactly this poll path for `ui.timer`.
   - **Trigger:** comment in `ui/core/run_control.py::process_docking_bridge`. The existing Future Work bullet was widened.
2. **A chat parse in flight can be applied to a session that has since changed → Stage 16.4.**
   - **Symptom:** `parse_message` takes `prior_workflow` before the blocking LLM call. If Load, New Session or `undo` replaces the parser's workflow meanwhile, `apply_parsed_message` validates the old merge and overwrites the new session's parser state.
   - **Why it can't happen yet:** Streamlit can't hit it, because the call blocks the script.
   - **Fix:** keep the `prior_workflow` object, and drop the reply if `state.parser.parsed_workflow is not prior_workflow`.
   - **Recommendation:** fix it in 16.4, together with the `run.io_bound` wiring that makes it reachable. Fixing it now would be untestable against a real caller.
   - **Trigger:** the `parse_message` docstring (`/code-review`).
3. **Uncached reads on the timer path → Stages 16.4/16.6.**
   - **Symptom:** `results_view`'s default `read_bytes` is uncached; Streamlit passes its mtime-keyed `st.cache_data` reader. A NiceGUI `ui.timer` that calls `pose_records()` or `read_job_log()` with the default would re-read every SDF file, or the whole `job.log`, per client per tick.
   - **Fix:** pass a reader cached on (path, mtime), and redo these reads only when the result or the file's size changes.
   - **Recommendation:** do it while wiring the timer. Nothing calls these on a timer today.
   - **Trigger:** comment at `ui/core/results_view.py::_read_path` (efficiency).

Deferred #4–#6 were first filed as declined. The owner moved them here
(2026-10-09) because each one waits on a condition ("if X happens"), and
rules §4 requires a trigger for those, not just a line in the report.

4. **A prep job that ends any other way drops a pending geometry command silently → Stage 16.5, if it adds a Stop for the prep job.**
   - **Symptom:** `process_box_picker_bridge` handles only completed and failed. Ending as "cancelled", or with no result and no error, clears the pending command with no chat reply, though the user was told "I'll set this" (`/code-review`).
   - **Why it can't happen yet:**
     - Nothing stops the prep bridge (only the docking bridge gets `request_stop`).
     - `execute_workflow` returns only completed, failed or cancelled, and cancelled needs the cancel event.
     - A result of None always comes with an error.
   - **Fix:** an `else` branch that sets the failure reply, with a test.
   - **Recommendation:** do it in the same change that adds a Stop for the prep job. Before that it can't be tested against a real path.
   - **Trigger:** comment in `ui/core/box_picker.py::process_box_picker_bridge`.
5. **`output_base` and `bridge_factory` are passed into every action separately → Stages 16.3–16.7, if the wiring gets repetitive** (altitude, offered as optional).
   - **Symptom:** every `ui/core` action takes the same two or three values. That is fine for one GUI calling a few of them, but it could become noise across NiceGUI's many handlers.
   - **Fix:** one per-app `GuiDeps(output_base, bridge_factory, …)`, built at startup and passed instead, in one change across `ui/core`.
   - **Recommendation:** leave it unless the NiceGUI wiring actually repeats them at many call sites. There is no second caller yet to justify it.
   - **Trigger:** comment under `BridgeFactory` in `ui/core/run_control.py`.
6. **Streamlit-shaped comments in `ui/core` → Stages 16.3–16.7** (altitude).
   - **Symptom:** remarks such as "runs before the layout", "must run before the widgets render" and "rerun" describe the Streamlit caller's constraints. A NiceGUI author could read them as rules of the core.
   - **Fix:** whoever wires a NiceGUI caller to a function carrying such a remark moves it to the Streamlit call site in `ui/app.py`, or rewords it. 16.8's repo-wide grep for "streamlit" catches the rest.
   - **Recommendation:** worth doing as each function is wired. 16.3 starts before the 16.8 grep, so waiting for the grep alone would be too late.
   - **Trigger:** the `ui/core/__init__.py` package docstring, which also states the rule.

### Declined, with reasons

- **`_render_pose_downloads` builds `dock_results_by_ligand` twice per rerun** (`/code-review`). The dicts hold at most a few hundred entries, so the cost can't be measured, and Streamlit is removed in 16.8.
- **The geometry command re-reads the receptor PDB without `st.cache_data`** (`/code-review`). This difference was declared in 16.2. It costs one read per command, not per rerun.
- **`_STATE_KEYS` could be a rename map** (`/code-review`, simplification). The explicit table is the adapter's whole contract, and the adapter is deleted in 16.8.
- **`format_summary` vs `benchmark_docking.format_stats`** (reuse). The two print different fields in a different order; merging them would change `benchmark_docking.py`'s established output for a two-line saving.
- **Test-only wrappers in `ui/session_state.py`, and the `_GREETING_MESSAGE` alias** (simplification). These are Streamlit adapter code, deleted in 16.8 together with their tests.
- **`apply_parsed_params`' six box assignments, and the repeated `protein_id` read in `collect_form_params`** (simplification). Also moved verbatim, and marginal.
- **The `start_prep` parameter of `protein_geometry_command`** (altitude). It exists only for `app.py`'s test wrapper; it goes with those tests in 16.8.
- **The adapter's `form` is all of `st.session_state`** (altitude). That is how the adapter works by design, and it is deleted in 16.8. The two-copies point (`params` vs `form`, joined by `sync_form_into_params`) is noted for 16.3's design, not as a defect.

---

## Exception-handling pass

Clean.
- Every handler added in the range was moved unchanged from code that earlier reviews covered (e.g. `rename_session`'s `except Exception` turns any `rename_job` failure into "Rename failed: …", a real boundary).
- The new ones are legitimate:
  - The `try` around reading the parent page's URL in `index.html`: a cross-origin parent throws, and then `?perf` is simply off.
  - The adapter's `KeyError` → `AttributeError` conversion.
- Quality #4 replaced two handlers with `load_json_dict`'s identical ones.

## 16.0 tooling re-check

- **Every number in `docs/GUI_GUIDE.md`'s baseline table matches** `docs/benchmarks/2026-10-07_gui_streamlit_{browser,stats}.json`:
  - box ± 491 / 899 ms;
  - `show history` 4,736 / 926 ms;
  - Load 1,026 ms;
  - CPU 10.1% / 2.0%;
  - memory 132–142 MiB;
  - render 190 ms.
- **The viewer's `index.html` diff only adds lines.** With `?perf` off, the cost is one null check per render, and the `postMessage` layer is untouched.

## Compatibility (this review's one shared-code change)

`WorkflowOrchestrator.__init__` gained `on_message`, a third parameter with a default.
- **Callers:** every one passes keywords and none passes a third positional argument: `nlp_orchestrator.py:1292`, the bridge, and 50+ test call sites (grep).
- **Images:** the orchestrator runs only in-process, in the CLI and the `ui` image (which mounts the repo). No MCP service image contains or calls it, so no image needs rebuilding.

---

## Verification

- **Regression tests fail before, pass after.**
  - **Method:** the four changed test files ran against `2df69ec`'s code in a temporary worktree (`git worktree add --detach`), which was removed afterwards.
  - **Fail on the old code, each because of its bug:**
    - the double prep (the second prep starts);
    - both cue tests (no `CueError`; a bare `ValueError`, nothing written);
    - `test_runs_leave_sys_stdout_alone` (`sys.stdout` is the proxy).
  - **Also fail on the old code, because the sink API doesn't exist there:** the five other sink tests.
  - **Pass on the old code, as a guard should:** the transitive import guard, since there was no leak.
- **Hermetic suite:** `docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/` gave 1221 passed, 20 skipped (from 1207 / 20; last rerun 2026-10-11, after Correctness #4–#6).
- **`ui` tier:** `docker compose run --rm --no-deps ui python -m pytest -m "not integration"` gave 1382 passed, 12 skipped, 2 deselected (last rerun 2026-10-11). The AppTest files changed only in the renamed pending-targets key (Correctness #5); `tests/test_ui_app_helpers.py` also changed in its autouse fixture (Quality #7).
- **Real data, headless (2026-10-08).**
  - **Setup:**
    - A side Streamlit container (`ui-check-r1`) ran this tree on the live network with `--volumes-from mcp-ui`.
    - LLM: local Ollama only. A throwaway settings file was used and no paid key was passed. No LLM call was made.
    - A scratchpad Playwright driver reused `benchmark_gui_browser.Driver` for a 3-ligand Local Files job, `job_20261008_101439_7b5049`.
  - **Geometry during a running prep:** "still preparing", then the centre was applied (Correctness #1).
  - **During the run:** "Load receptor for box picking" was clicked again once STEP 4 showed, so two bridges ran at once. The run's chat message has STEP 1–5 and "Protein prepared successfully" exactly once each. "WORKFLOW COMPLETED" appears 0 times; that is Deferred #1(a), pre-existing.
  - **After the run:** the completed reply arrived, `show history` was answered, and there were no page errors.
  - **Cleaned up:**
    - the side container;
    - all four job dirs it created (an earlier attempt's prep job, the run, and two prep jobs);
    - the `ligands3_r1.sdf` upload.
    - `uploads/receptor.pdb` was kept: it is byte-identical to the one 16.0 left there. `orchestrator_output/` lists 184 entries, as before.
- **Live `ui` restarted** (`docker restart mcp-ui`, 16.1's lesson: `app.py`'s imports changed). It is healthy, and the headless smoke check passed: `show history` replied, there were no exceptions or page errors, and 25 Load buttons showed.
- **Fresh stack (2026-10-11).** This closes the step Phase 15 R1 left to the owner (`stage_15_r1.md`, "Fresh stack"), on the owner's request.
  - **Built first, to keep downtime short:** `mcp-docking-base` (`--profile dev build script-runner`), then every service image.
  - **Down and up** under the live project's own name: `docker compose -p af6ad48c… down`, then `up -d`.
    - Keeping that name kept the Ollama volume (`af6ad48c…_ollama-data`, `gemma3:4b`) and the container and network names.
    - A plain `up` from the repo would have started project `docking_platform`, with a different volume.
    - `down` was run without `-v`, so no volume was removed.
  - **Result:** all 8 services healthy.
  - **Checks on the stack's own network**, with LLM calls pointed at the local Ollama:
    - `nlp_orchestrator.py --check-health`: all healthy;
    - `tests/test_docking_stop_integration.py`: 1 passed. It skipped in earlier runs because the stack ran under another project.
  - **Real run, driven headlessly through the fresh `mcp-ui`:**
    - The job: 2 ligands, the box picker's receptor prep, "set box center as protein center" and "look across all protein", then `use vina`, Parallel on, Run Docking.
    - **Geometry:** the first command waited for the prep ("still preparing") and then applied the centre. The prep had finished before the second command arrived, so it applied immediately; the two-command queue is covered by its unit tests (#5).
    - **Run** `job_20261011_173341_6e66ee`:
      - the chat message has "WORKFLOW COMPLETED SUCCESSFULLY" once (#6);
      - `results.dock` records `docking_algorithm: vina` and `workers: 2` (Phase 15's key), with 2 of 2 docked (-6.90 and -6.03 kcal/mol);
      - no page errors.
    - Two earlier attempts stopped at a driver selector (the Parallel checkbox is inside the collapsed Advanced section); neither was an app problem.
  - **Cleaned up:** all four job dirs from these attempts and the `ligands2_r1.sdf` upload. `orchestrator_output/` lists 184 entries, as before.
