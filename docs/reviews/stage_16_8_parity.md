# Stage 16.8 — NiceGUI parity sign-off

Phase 16's migration rule says Streamlit is removed only once the owner's live-browser check confirms parity. This file holds that check:

1. **The feature list** the migration had to cover. It was the owner's requirement of 2026-10-05. It is recorded here because the repo never had a copy.
2. **The browser checklist**, one block per feature, for the owner to run in the NiceGUI GUI.
3. **The test parity table.** Each Streamlit test maps to its NiceGUI or core counterpart. It is moved here from `tests/test_nicegui_parity.py` when the Streamlit tests are deleted.

## 1. Feature list (owner's requirement, 2026-10-05)

**Parity first, no new features.** Port everything the current GUI does:
- input modes (RCSB, local files, folder)
- parameters and Advanced settings, including parallel docking
- chat commands
- box picker and the Docking Box fields
- run, stop and redock
- live progress and the job log
- results: affinity chart, 3D viewer, pose pickers, downloads, comparison table
- session history: load, rename, delete, revisions, undo, save results as
- settings (LLM provider, Ollama models)

**Layout:**
- chat in a persistent, collapsible side drawer;
- tabs Setup / Results / Sessions;
- switch to Results automatically when a run finishes;
- resizable panels (the multi-monitor fix).

**Viewer:** it keeps its scene and camera between updates, and a click goes straight to the box fields.

**Live updates:** by a timer, not by redrawing the page. `ui.aggrid` holds the ligand and pose lists, and downloads are served through routes.

**Technical:**
- per-user state, with nothing blocking the event loop;
- a non-root container with the same mounts.

**Out of scope:** reports, multi-user auth, new analysis.

## 2. Browser checklist

**Where:** `http://localhost:3001`, the `ui-nice` service. Streamlit is still on `http://localhost:3000` for side-by-side comparison.

**Files:** `data/benchmark_gui/receptor.pdb`, `data/benchmark_gui/actives_first25.sdf` and `data/ligands/*.sdf`.

**Before you hand it over:**
- Mark each row ✅ / ❌, and note anything odd.
- Each ❌ is fixed before the cutover.
- Every session the checklist creates can be deleted at the end, in step 34.

### Layout

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 1 | Open the page. | Header with title, Settings and Chat buttons. Chat drawer open on the right, with the greeting. Tabs Setup / Results / Sessions, Setup selected. | |
| 2 | Click the Chat button twice. | The drawer hides, then comes back with its messages intact. | |
| 3 | On Setup, drag the splitter between the form and the viewer side. | Both sides resize. With a receptor loaded (row 12), the viewer follows the new width. | |

### Input modes and parameters

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 4 | **RCSB IDs:** Protein ID `6lu7`, Ligand IDs `ATP`. | "Current parameters" shows the protein and ligand. | |
| 5 | **Local Files:** drop `receptor.pdb`, then `actives_first25.sdf`. | "Protein: receptor.pdb", and "1 ligand file(s) ready". The multi-molecule SDF is split into its 25 molecules when the run prepares the ligands (row 25 shows 25). **Clear** empties each one. | |
| 6 | **Batch Processing:** Protein ID `6lu7`, then drop two or three files from `data/ligands/` in two drops. | The count adds up across the drops. Dropping the same file name again replaces it rather than adding a second copy. | |
| 7 | Docking Box: type the Center X/Y/Z and Box X/Y/Z values. | The summary shows them. | |
| 8 | Advanced: switch the algorithm between Moldina (PSO) and Vina. | With Vina: CPUs per ligand plus the Vina caption. With Moldina: the PSO caption instead. pH and Exhaustiveness are editable. | |
| 9 | Advanced: tick Parallel ligand docking, then set Max parallel workers above the cap. | Workers is enabled only while Parallel is ticked. The worker-cap caption is shown, and the value is clamped to the bound. | |
| 10 | Session name: type a name. | It is limited in length and appears in the summary/session label once a run starts. | |

### Chat commands

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 11 | Type a free-text request, e.g. "dock ATP into 6lu7" (local Ollama). | A "Parsing with LLM..." spinner, then "Got it!" with the fields filled in. | |
| 12 | `use vina`, then `use moldina`. | A reply each time, and the Advanced algorithm radio follows. | |
| 13 | With no receptor loaded: `set box center as protein center`. | "fetching and preparing it now". Later "Protein center set to (…)", with Center X/Y/Z filled in. | |
| 14 | `look across all protein`. | Center and box size are set from the whole protein. | |

### Box picker

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 15 | Local Files inputs from row 5, then **Load receptor for box picking**. | A spinner, then the 3D viewer with the receptor and the box. | |
| 16 | Rotate or zoom the viewer, then click X + / X −, Y and Z. | The box resizes **and the camera stays where you left it**. The Box fields change by 1 Å per click. | |
| 17 | Click a receptor atom. | Center X/Y/Z become that atom's coordinates, and the caption updates. | |
| 18 | Type a new Center X. | The box moves in the viewer. | |
| 19 | **Discard picked receptor**. | The viewer goes, and the Load button is back. | |

### Run, stop, redock, progress

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 20 | **Run Docking** on the Local Files inputs (all 25, Moldina). | The run card: "Session … is running...", a progress bar "N / 25 ligands docked" and "Currently docking: …". Run Docking is disabled while it runs. The chat's **Run log** expander streams STEP lines. | |
| 21 | Let it finish. | The page switches to **Results** by itself. The chat says the run completed, and the run log shows "WORKFLOW COMPLETED SUCCESSFULLY" once. | |
| 22 | Change one parameter (e.g. Box X), then **Run Docking** again. | The **Confirm redock** dialog lists the steps to rerun. Cancel closes it with nothing run. Confirm starts the redock. | |
| 23 | During a run, **Stop**. | "Stop requested — stopping…", and the run ends within a few seconds as stopped. | |
| 24 | **Save Session** with no change, then with a change; also **New Session** and **Reset**. | "No parameter changes to save." / "Parameters saved…". New Session keeps the parameters and starts afresh. Reset clears everything. | |

### Results

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 25 | Results tab after row 21. | The status line, "Docked with Moldina (PSO)", and the affinity chart with 25 ligands. | |
| 26 | Click a row in the ligand grid, then one in the pose grid. | The 3D viewer shows that ligand's pose in the receptor. The pose caption reads "Mode N (… kcal/mol)". | |
| 27 | Downloads: change the pose format, then click a pose download and one other file. | Each file downloads with its real name and contents. | |
| 28 | **Session Log** expander, then **Download full log**. | The log's tail is shown, and the whole `job.log` downloads. | |
| 29 | **Save results as…** with `trial_a`, then save `trial_a` again; also try `save results as trial_b` in chat. | The first save succeeds. The second shows the conflict choices (Overwrite / Save as `trial_a_HHMMSS` / Cancel), and each works. The chat command saves too. | |

### Sessions

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 30 | Sessions tab. | Every session is listed (not only the newest 10), plus a Failed Sessions list, and the active session's card. | |
| 31 | **Rename** a session (Save), then **Load** it. | The new name shows in the row, the chat label and the Results status. Load switches to Results and shows that session. | |
| 32 | Revision history: `show history` in chat, then `undo`, then **Restore revision** on an older one. | The table and current revision match the chat's reply. Undo and Restore bring the old parameters back into the form. | |
| 33 | Tick two sessions → **Compare selected sessions**. | Results shows the comparison table, with one column per session. | |
| 34 | **Delete** each test session from this checklist → Confirm. | It disappears from the list. Cancel leaves it. | |

### Settings

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 35 | Settings → provider Ollama. | The Model list is filled from the local Ollama models (or "no models" if Ollama is down). **Apply settings** saves the setting, and a reload keeps it. | |
| 36 | Settings → a provider whose client can't be built (e.g. one needing an API key, with the key left empty) → Apply. | An error is shown, and nothing is saved: the previous settings stay in use. | |

### Headless pre-run (2026-10-25)

Before this checklist was handed over, a Playwright script ran it against
the live `mcp-ui-nice` (headless Chromium 1600 × 1000, on the GUI
container's network). It was a scratch script, not in the repo. Its run used:
- the receptor plus 3 ligands from `data/ligands/` (25 for row 5's upload);
- a reference session for Compare;
- a cleanup of every session, prep job and upload it created.

The result: 34 of 34 automated rows passed, with no page errors.
- **Row 16:** each step logged `[perf] … mode=box`, a box-only redraw.
  That is the code path that keeps the camera; the owner confirmed the
  camera itself in 16.5.
- **Row 17:** a canvas click hit an atom, and Center X changed.
- **Row 27:** every download link, 16 of them in 4 pose formats, was
  served with content.
- **Row 30:** 187 sessions were listed.
- **Row 33:** the comparison had one column per session.

Left to the owner:
- **Row 11:** needs the LLM.
- **Row 35:** only opened, not applied, so the owner's saved settings
  stay as they are.
- **Row 36:** would change the settings.
- **Anything that needs eyes:** the layout's look, the camera, the chart.

The pre-run also surfaced one behaviour, the same in both GUIs, so it is
not a parity gap:
- A **redock stopped with Stop** marks the whole session cancelled. Its
  Results then read "Session … failed. Workflow cancelled by user
  request", and the previous run's results are no longer shown.
- `workflow_results.json` is rewritten by the redock, as it is from
  Streamlit. Whether the earlier pose files survive wasn't checked
  before the test session was deleted.

### Sign-off

- [x] Signed off by the owner on 2026-10-26 ("looks ok"), after their browser check and before the cutover. Row 11: the chat fills in the parameters but does not start docking, which is by design in both GUIs; docking starts from **Run Docking**. The owner asked about it, and it is recorded under Future Work in `dev_plan.md`.

## 3. Test parity table

Every test of the deleted Streamlit tier, with what covers it now (128 rows: 62 ported, 53 core, 13 N/A). The guard tests in `tests/test_nicegui_parity.py` held this table to the Streamlit test files until they were deleted; at that point every row was mapped and every target existed (hermetic suite, 2026-10-25).

Targets:
- `ported: <file>::<test>`: the NiceGUI test that replaces it.
- `core: <file>::<test>`: the case tests the Streamlit wrapper or adapter
  of `ui/core` logic, and that hermetic core test covers the same case.
  NiceGUI calls `ui/core` directly, so there is nothing else to port.
- `N/A`: a Streamlit-only quirk; the note says why.

| Streamlit test | Target | Note |
|---|---|---|
| test_ui_apptest.py::test_fresh_page_renders_without_exception | ported: test_nicegui_layout.py::test_page_shows_title_tabs_and_greeting | |
| test_ui_apptest.py::test_worker_cap_from_compose_shows_limit_caption | ported: test_nicegui_setup.py::test_worker_cap_from_compose_shows_limit_caption |  |
| test_ui_apptest.py::test_worker_cap_fallback_shows_warning_with_reason | ported: test_nicegui_setup.py::test_worker_cap_fallback_shows_warning_with_reason |  |
| test_ui_apptest.py::test_box_picker_passes_center_and_size_to_viewer | ported: test_nicegui_box_picker.py::test_box_picker_passes_center_and_size_to_viewer |  |
| test_ui_apptest.py::test_box_picker_without_receptor_offers_load_button | ported: test_nicegui_box_picker.py::test_box_picker_without_receptor_offers_load_button |  |
| test_ui_apptest.py::test_deferred_geometry_command_applies_and_shows_reply | ported: test_nicegui_box_picker.py::test_deferred_geometry_command_applies_and_shows_reply |  |
| test_ui_apptest.py::test_deferred_geometry_command_reports_failed_fetch | ported: test_nicegui_box_picker.py::test_deferred_geometry_command_reports_failed_fetch | Streamlit shows the error for one rerun; NiceGUI keeps it until the next Load |
| test_ui_apptest.py::test_summary_shows_docking_box_fields_not_stale_parsed_params | ported: test_nicegui_setup.py::test_summary_shows_docking_box_fields_not_stale_parsed_params |  |
| test_ui_apptest.py::test_box_size_step_updates_summary_and_viewer | ported: test_nicegui_box_picker.py::test_box_size_step_updates_summary_and_viewer |  |
| test_ui_apptest.py::test_summary_updates_after_visual_center_pick | ported: test_nicegui_box_picker.py::test_summary_updates_after_visual_center_pick |  |
| test_ui_apptest.py::test_typing_center_in_docking_box_moves_picker_box | ported: test_nicegui_box_picker.py::test_typing_center_in_docking_box_moves_picker_box |  |
| test_ui_apptest.py::test_typing_box_size_in_docking_box_resizes_picker_box | ported: test_nicegui_box_picker.py::test_typing_box_size_in_docking_box_resizes_picker_box |  |
| test_ui_apptest.py::test_undrawable_box_size_shows_placeholder_without_crash | ported: test_nicegui_box_picker.py::test_undrawable_box_size_shows_placeholder_without_crash |  |
| test_ui_apptest.py::test_small_positive_box_size_is_drawn_as_typed | ported: test_nicegui_box_picker.py::test_small_positive_box_size_is_drawn_as_typed |  |
| test_ui_apptest.py::test_redock_local_files_session_after_ligand_change | ported: test_nicegui_setup.py::test_redock_local_files_session_after_ligand_change |  |
| test_ui_apptest.py::test_finished_run_reply_is_rendered_in_chat | ported: test_nicegui_setup.py::test_finished_run_reply_is_rendered_in_chat |  |
| test_ui_apptest.py::test_completed_session_from_disk_renders_results_and_viewer | ported: test_nicegui_results.py::test_completed_session_from_disk_renders_results_and_viewer |  |
| test_ui_apptest.py::test_sidebar_lists_completed_and_failed_sessions | ported: test_nicegui_sessions.py::test_sidebar_lists_completed_and_failed_sessions | Sessions tab, not a sidebar; it lists every session, not the 10 newest |
| test_ui_apptest.py::test_revision_history_expander_lists_revisions | ported: test_nicegui_sessions.py::test_revision_history_expander_lists_revisions | on the Sessions tab |
| test_ui_apptest.py::test_revision_history_hidden_without_active_session | ported: test_nicegui_sessions.py::test_revision_history_hidden_without_active_session |  |
| test_ui_apptest.py::test_restore_revision_loads_its_params_and_marks_all_pending | ported: test_nicegui_sessions.py::test_restore_revision_loads_its_params_and_marks_all_pending |  |
| test_ui_apptest.py::test_redock_after_restore_plans_full_rerun | ported: test_nicegui_sessions.py::test_redock_after_restore_plans_full_rerun |  |
| test_ui_apptest.py::test_cancelled_redock_plan_is_offered_again | ported: test_nicegui_setup.py::test_cancelled_redock_plan_is_offered_again |  |
| test_ui_apptest.py::test_finished_run_clears_pending_only_when_completed | ported: test_nicegui_setup.py::test_finished_run_clears_pending_only_when_completed |  |
| test_ui_apptest.py::test_restore_disabled_while_job_runs | ported: test_nicegui_sessions.py::test_restore_disabled_while_job_runs |  |
| test_ui_apptest.py::test_chat_undo_refused_while_job_runs | ported: test_nicegui_chat.py::test_chat_undo_refused_while_job_runs |  |
| test_ui_apptest.py::test_chat_show_history_and_undo | ported: test_nicegui_chat.py::test_chat_show_history_and_undo |  |
| test_ui_apptest.py::test_param_widgets_survive_early_rerun_in_chat_column | N/A | Streamlit drops widgets a rerun stops before drawing; NiceGUI draws each page once |
| test_ui_apptest.py::test_chat_history_without_active_session | ported: test_nicegui_chat.py::test_chat_history_without_active_session |  |
| test_ui_apptest.py::test_save_results_panel_writes_bundle | ported: test_nicegui_results.py::test_save_results_panel_writes_bundle |  |
| test_ui_apptest.py::test_save_results_blocked_while_parameters_pending | ported: test_nicegui_results.py::test_save_results_blocked_while_parameters_pending | restored through the chat's `undo 1`; the Sessions tab's Restore is tested in test_nicegui_sessions.py |
| test_ui_apptest.py::test_save_results_blocked_when_parameters_never_ran_after_load | ported: test_nicegui_results.py::test_save_results_blocked_when_parameters_never_ran_after_load |  |
| test_ui_apptest.py::test_chat_save_refused_while_job_runs | ported: test_nicegui_chat.py::test_chat_save_refused_while_job_runs |  |
| test_ui_apptest.py::test_save_results_conflict_overwrite_and_new_name | ported: test_nicegui_results.py::test_save_results_conflict_overwrite_and_new_name |  |
| test_ui_apptest.py::test_chat_save_docking_as_name | ported: test_nicegui_chat.py::test_chat_save_docking_as_name |  |
| test_ui_apptest.py::test_file_downloads_exclude_saved_bundles | ported: test_nicegui_results.py::test_file_downloads_exclude_saved_bundles | checks the download links, which go through the /files route |
| test_ui_apptest.py::test_save_bundle_name_survives_early_rerun_in_chat_column | N/A | Streamlit drops widgets a rerun stops before drawing; NiceGUI draws each page once |
| test_ui_apptest.py::test_save_results_sits_between_viewer_and_session_log | ported: test_nicegui_results.py::test_save_results_sits_between_viewer_and_session_log | re-specified for the tab: viewer and pose grids, then Save results as…, then the Session Log, then the downloads |
| test_ui_apptest.py::test_receptor_load_with_box_fields_set_logs_no_session_state_warning | N/A | Streamlit's warning for a widget given both a default and a Session State value |
| test_ui_apptest.py::test_redock_after_load_of_never_run_restore_plans_full_rerun | ported: test_nicegui_sessions.py::test_redock_after_load_of_never_run_restore_plans_full_rerun |  |
| test_ui_apptest.py::test_redock_after_stopped_first_run_docks_again | ported: test_nicegui_setup.py::test_redock_after_stopped_first_run_docks_again |  |
| test_ui_apptest.py::test_chat_undo_of_state_only_revision_is_refused | ported: test_nicegui_chat.py::test_chat_undo_of_state_only_revision_is_refused |  |
| test_ui_apptest.py::test_restore_drops_box_picker_receptor_only_for_another_protein | ported: test_nicegui_sessions.py::test_restore_drops_box_picker_receptor_only_for_another_protein |  |
| test_ui_apptest.py::test_finished_run_reply_shows_without_rerun | N/A | Streamlit needs the reply drawn in the same script run; NiceGUI pushes updates. The reply itself: test_nicegui_setup.py::test_finished_run_reply_is_rendered_in_chat |
| test_ui_apptest.py::test_chat_command_reply_shows_without_rerun | N/A | Streamlit needs the reply drawn in the same script run; NiceGUI pushes updates. The replies themselves: test_nicegui_chat.py |
| test_ui_apptest.py::test_run_docking_with_missing_params_replies_without_rerun | ported: test_nicegui_setup.py::test_run_docking_with_missing_params_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_redock_with_nothing_changed_replies_without_rerun | ported: test_nicegui_setup.py::test_redock_with_nothing_changed_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_save_session_replies_without_rerun | ported: test_nicegui_setup.py::test_save_session_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_save_session_saves_a_real_change_with_defaults | ported: test_nicegui_sessions.py::test_save_session_saves_a_real_change_with_defaults | its Load half through the Sessions tab |
| test_ui_apptest.py::test_save_session_with_form_back_at_checkpoint_clears_stale_draft | ported: test_nicegui_sessions.py::test_save_session_with_form_back_at_checkpoint_clears_stale_draft | its Load half through the Sessions tab |
| test_ui_apptest.py::test_typed_center_survives_after_a_viewer_click | ported: test_nicegui_box_picker.py::test_typed_center_survives_after_a_viewer_click | a NiceGUI click is an event, sent once; the typed center must still survive the redraws |
| test_ui_apptest.py::test_clicking_the_same_atom_again_after_typing_is_applied | ported: test_nicegui_box_picker.py::test_clicking_the_same_atom_again_after_typing_is_applied | no clickId needed: each click is its own event |
| test_ui_apptest.py::test_chat_parsed_box_moves_the_picker_box | ported: test_nicegui_box_picker.py::test_chat_parsed_box_moves_the_picker_box |  |
| test_ui_apptest.py::test_size_step_with_size_unset_draws_the_stepped_box | ported: test_nicegui_box_picker.py::test_size_step_with_size_unset_draws_the_stepped_box |  |
| test_ui_apptest.py::test_click_on_undrawable_box_docks_the_box_shown | ported: test_nicegui_box_picker.py::test_click_on_undrawable_box_docks_the_box_shown |  |
| test_ui_apptest.py::test_job_finishing_mid_run_is_handled_by_one_more_poll | ported: test_nicegui_setup.py::test_job_finishing_mid_run_is_handled_by_one_more_poll | the poll is the ui.timer |
| test_ui_apptest.py::test_cpu_field_shows_only_for_vina_and_bounds_workers | ported: test_nicegui_setup.py::test_cpu_field_shows_only_for_vina_and_bounds_workers |  |
| test_ui_apptest.py::test_switching_algorithm_redocks_dock_and_visualize_only | ported: test_nicegui_setup.py::test_switching_algorithm_redocks_dock_and_visualize_only |  |
| test_ui_apptest.py::test_pso_mode_sends_one_cpu_whatever_the_cpu_field_holds | ported: test_nicegui_setup.py::test_pso_mode_sends_one_cpu_whatever_the_cpu_field_holds |  |
| test_ui_apptest.py::test_loaded_pre_phase15_session_reads_as_moldina_with_nothing_to_redock | ported: test_nicegui_sessions.py::test_loaded_pre_phase15_session_reads_as_moldina_with_nothing_to_redock |  |
| test_ui_apptest.py::test_chat_parse_without_algorithm_keeps_the_selector | ported: test_nicegui_chat.py::test_chat_parse_without_algorithm_keeps_the_selector |  |
| test_ui_apptest.py::test_chat_parse_naming_an_unknown_algorithm_renders_without_crash | ported: test_nicegui_chat.py::test_chat_parse_naming_an_unknown_algorithm_renders_without_crash |  |
| test_ui_apptest.py::test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph | ported: test_nicegui_chat.py::test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph |  |
| test_ui_apptest.py::test_chat_algorithm_command_moves_the_selector_and_resets_workers | ported: test_nicegui_chat.py::test_chat_algorithm_command_moves_the_selector_and_resets_workers |  |
| test_ui_apptest.py::test_summary_shows_the_selected_algorithm | ported: test_nicegui_setup.py::test_summary_shows_the_selected_algorithm |  |
| test_ui_apptest.py::test_results_name_the_algorithm_that_docked | ported: test_nicegui_results.py::test_results_name_the_algorithm_that_docked |  |
| test_ui_apptest.py::test_run_log_is_shown_apart_from_the_chat | ported: test_nicegui_chat.py::test_run_log_streams_apart_from_the_chat | |
| test_ui_app_helpers.py::test_collect_form_params_rcsb_mode | core: test_ui_core_state.py::test_collect_form_params_rcsb_mode | |
| test_ui_app_helpers.py::test_collect_form_params_omits_center_when_incomplete | core: test_ui_core_state.py::test_collect_form_params_omits_incomplete_box_and_clamps_workers | |
| test_ui_app_helpers.py::test_collect_form_params_max_workers_clamped_to_at_least_1 | core: test_ui_core_state.py::test_collect_form_params_omits_incomplete_box_and_clamps_workers | |
| test_ui_app_helpers.py::test_collect_form_params_folder_mode_skips_ligand_text_field | core: test_ui_core_state.py::test_collect_form_params_folder_mode_skips_ligand_text_field | |
| test_ui_app_helpers.py::test_apply_viewer_click_writes_center_and_keeps_a_set_size | core: test_ui_core_box_picker.py::test_viewer_click_writes_center_and_keeps_a_drawable_size | |
| test_ui_app_helpers.py::test_apply_viewer_click_fills_in_the_shown_size_when_unset | core: test_ui_core_box_picker.py::test_viewer_click_fills_in_the_placeholder_size | |
| test_ui_app_helpers.py::test_apply_viewer_click_replaces_an_undrawable_size_with_the_placeholder | core: test_ui_core_box_picker.py::test_viewer_click_fills_in_the_placeholder_size | |
| test_ui_app_helpers.py::test_step_box_size_changes_one_axis | core: test_ui_core_box_picker.py::test_step_changes_one_axis_and_stops_at_the_minimum | |
| test_ui_app_helpers.py::test_step_box_size_stops_at_the_minimum | core: test_ui_core_box_picker.py::test_step_changes_one_axis_and_stops_at_the_minimum | |
| test_ui_app_helpers.py::test_step_box_size_steps_from_the_placeholder_when_undrawable | core: test_ui_core_box_picker.py::test_step_from_the_placeholder_writes_all_three_axes | |
| test_ui_app_helpers.py::test_start_box_picker_prep_reports_missing_protein_source | core: test_ui_core_box_picker.py::test_prep_without_a_protein_source_warns_and_starts_nothing | |
| test_ui_app_helpers.py::test_start_box_picker_prep_reports_validation_error_without_a_parser | core: test_ui_core_box_picker.py::test_prep_validation_error_is_reported | |
| test_ui_app_helpers.py::test_find_receptor_pdb_prefers_box_picker_receptor | core: test_ui_core_box_picker.py::test_find_receptor_prefers_the_picker_then_the_last_run | |
| test_ui_app_helpers.py::test_find_receptor_pdb_falls_back_to_session_last_results | core: test_ui_core_box_picker.py::test_find_receptor_prefers_the_picker_then_the_last_run | |
| test_ui_app_helpers.py::test_find_receptor_pdb_returns_none_when_nothing_loaded | core: test_ui_core_box_picker.py::test_find_receptor_none_when_nothing_loaded_or_file_gone | |
| test_ui_app_helpers.py::test_find_receptor_pdb_returns_none_when_file_missing_on_disk | core: test_ui_core_box_picker.py::test_find_receptor_none_when_nothing_loaded_or_file_gone | |
| test_ui_app_helpers.py::test_handle_protein_geometry_command_no_protein_source_reports_whats_missing | core: test_ui_core_chat.py::test_geometry_command_with_no_protein_source_says_what_is_missing | |
| test_ui_app_helpers.py::test_handle_protein_geometry_command_defers_when_fetch_starts | core: test_ui_core_chat.py::test_geometry_command_without_a_receptor_starts_the_prep_and_waits | |
| test_ui_app_helpers.py::test_handle_protein_geometry_command_center | core: test_ui_core_box_picker.py::test_apply_geometry_sets_one_field_group | |
| test_ui_app_helpers.py::test_handle_protein_geometry_command_box_size | core: test_ui_core_box_picker.py::test_apply_geometry_sets_one_field_group | |
| test_ui_app_helpers.py::test_handle_protein_geometry_command_malformed_pdb_reports_error | core: test_ui_core_box_picker.py::test_apply_geometry_reports_an_unreadable_pdb | |
| test_ui_app_helpers.py::test_on_chat_submit_geometry_command_does_not_touch_llm_parser | core: test_ui_core_chat.py::test_geometry_command_uses_a_loaded_receptor | chat.submit takes no LLM parser at all |
| test_ui_app_helpers.py::test_on_chat_submit_algorithm_command_skips_llm | core: test_ui_core_chat.py::test_algorithm_command_switches_the_selector | chat.submit takes no LLM parser at all |
| test_ui_app_helpers.py::test_on_chat_submit_llm_default_does_not_reset_algorithm | core: test_ui_core_chat.py::test_only_named_parameters_reach_the_form | |
| test_ui_app_helpers.py::test_on_chat_submit_llm_explicit_algorithm_applied_and_echoed | core: test_ui_core_chat.py::test_named_algorithm_is_applied_and_echoed | |
| test_ui_app_helpers.py::test_on_chat_submit_first_parse_without_algorithm_keeps_choice | core: test_ui_core_chat.py::test_only_named_parameters_reach_the_form | |
| test_ui_app_helpers.py::test_save_upload_creates_an_upload_dir_every_uid_can_write | core: test_ui_core_uploads.py::test_save_upload_creates_an_upload_dir_every_uid_can_write |  |
| test_ui_session_state.py::test_init_seeds_expected_defaults | core: test_ui_core_state.py::test_new_session_has_greeting_empty_params_and_default_form | |
| test_ui_session_state.py::test_init_does_not_clobber_existing_state_on_a_second_call | N/A | Streamlit runs init() on every rerun; NiceGUI builds one GuiSession per page |
| test_ui_session_state.py::test_get_params_returns_empty_dict_when_unset | core: test_ui_core_state.py::test_new_session_has_greeting_empty_params_and_default_form | GuiSession.params is a plain field |
| test_ui_session_state.py::test_get_params_returns_current_params | N/A | accessor of the Streamlit adapter; GuiSession.params is a plain field |
| test_ui_session_state.py::test_apply_parsed_params_updates_params_dict_and_widget_keys | core: test_ui_core_state.py::test_apply_parsed_params_writes_params_and_form | |
| test_ui_session_state.py::test_apply_parsed_params_ignores_none_values | core: test_ui_core_state.py::test_apply_parsed_params_keeps_values_parsed_as_none | |
| test_ui_session_state.py::test_apply_parsed_params_skips_center_widget_sync_when_not_exactly_3_values | core: test_ui_core_state.py::test_apply_parsed_params_skips_a_box_field_group_without_three_values | |
| test_ui_session_state.py::test_apply_parsed_params_skips_box_widget_sync_when_not_exactly_3_values | core: test_ui_core_state.py::test_apply_parsed_params_skips_a_box_field_group_without_three_values | |
| test_ui_session_state.py::test_load_session_params_replaces_params_entirely | core: test_ui_core_state.py::test_load_session_params_resets_fields_the_loaded_params_lack | |
| test_ui_session_state.py::test_load_session_params_resets_widgets_the_loaded_params_lack | core: test_ui_core_state.py::test_load_session_params_resets_fields_the_loaded_params_lack | |
| test_ui_session_state.py::test_max_workers_default_is_measured_best_clamped_to_cap | core: test_ui_core_state.py::test_new_session_has_greeting_empty_params_and_default_form | |
| test_ui_session_state.py::test_load_session_params_sets_parallel_widgets_clamped | core: test_ui_core_state.py::test_load_session_params_clamps_workers_to_cap_divided_by_cpu | |
| test_ui_session_state.py::test_init_selects_moldina | core: test_ui_core_state.py::test_new_session_has_greeting_empty_params_and_default_form | |
| test_ui_session_state.py::test_effective_cpu | core: test_ui_core_state.py::test_effective_cpu | |
| test_ui_session_state.py::test_switching_algorithm_resets_workers_to_its_best | core: test_ui_core_state.py::test_switching_algorithm_resets_workers_to_its_best | |
| test_ui_session_state.py::test_switch_at_several_cpus_per_ligand_stays_within_quota | core: test_ui_core_state.py::test_switch_at_several_cpus_per_ligand_stays_within_quota | |
| test_ui_session_state.py::test_selecting_the_current_algorithm_keeps_workers | core: test_ui_core_state.py::test_selecting_the_current_algorithm_keeps_workers | |
| test_ui_session_state.py::test_apply_parsed_params_without_algorithm_keeps_the_selector | core: test_ui_core_state.py::test_apply_parsed_params_without_algorithm_keeps_the_selector | |
| test_ui_session_state.py::test_apply_parsed_params_with_algorithm_moves_the_selector | core: test_ui_core_state.py::test_apply_parsed_params_with_algorithm_moves_the_selector | |
| test_ui_session_state.py::test_apply_parsed_params_ignores_an_unrecognised_algorithm | core: test_ui_core_state.py::test_apply_parsed_params_ignores_an_unrecognised_algorithm | |
| test_ui_session_state.py::test_load_of_session_without_algorithm_reads_as_moldina | core: test_ui_core_state.py::test_load_of_session_without_algorithm_reads_as_moldina | |
| test_ui_session_state.py::test_load_of_vina_session_keeps_its_saved_workers | core: test_ui_core_state.py::test_load_of_vina_session_keeps_its_saved_workers | |
| test_ui_session_state.py::test_load_clamps_workers_to_cap_divided_by_cpu | core: test_ui_core_state.py::test_load_session_params_clamps_workers_to_cap_divided_by_cpu | |
| test_ui_session_state.py::test_clear_box_picker_state_removes_picker_keys_but_not_the_box | core: test_ui_core_state.py::test_clear_box_picker_state_keeps_the_box | |
| test_ui_session_state.py::test_clear_box_picker_state_is_a_noop_when_nothing_to_clear | core: test_ui_core_state.py::test_clear_box_picker_state_is_a_noop_when_nothing_to_clear | |
| test_ui_session_state.py::test_start_new_session_resets_job_identity_and_chat | core: test_ui_core_state.py::test_start_new_session_detaches_but_keeps_the_form | |
| test_ui_session_state.py::test_start_new_session_clears_stale_box_picker_receptor | core: test_ui_core_state.py::test_start_new_session_detaches_but_keeps_the_form | |
| test_ui_session_state.py::test_start_new_session_preserves_parameter_widgets | core: test_ui_core_state.py::test_start_new_session_detaches_but_keeps_the_form | |
| test_ui_streamlit_adapter.py::test_view_maps_every_gui_session_field | N/A | tests the Streamlit adapter itself, removed in 16.8 |
| test_ui_streamlit_adapter.py::test_view_reads_and_writes_the_existing_keys | N/A | tests the Streamlit adapter itself, removed in 16.8 |
| test_ui_streamlit_adapter.py::test_none_removes_absent_style_keys_but_stores_plain_ones | N/A | tests the Streamlit adapter itself, removed in 16.8 |
| test_ui_streamlit_adapter.py::test_absent_keys_read_as_defaults | N/A | tests the Streamlit adapter itself, removed in 16.8 |
| test_ui_streamlit_adapter.py::test_form_is_session_state_and_cap_is_read_live | N/A | tests the Streamlit adapter itself, removed in 16.8 |
| test_ui_streamlit_adapter.py::test_unknown_attribute_raises | N/A | tests the Streamlit adapter itself, removed in 16.8 |
