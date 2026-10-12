"""Streamlit -> NiceGUI test parity table (Phase 16, finding 6).

Every test of the Streamlit tier maps to exactly one row below, so none is
dropped silently when Streamlit goes in Stage 16.8. The tests at the bottom
check that. They read the test files with ``ast`` and import no GUI
framework, so they run in the hermetic suite and in CI.

Targets:
- ``16.6`` ... ``16.7``: to be ported as a NiceGUI User-fixture test in that
  stage (pending). When ported, the row changes to ``ported: <file>::<test>``.
- ``ported: <file>::<test>``: the NiceGUI test that replaces it.
- ``core: <file>::<test>``: the case tests the Streamlit wrapper or adapter
  of ``ui/core`` logic, and that hermetic core test covers the same case.
  NiceGUI calls ``ui/core`` directly, so there is nothing else to port.
- ``N/A``: a Streamlit-only quirk; the note says why.

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
| test_ui_apptest.py::test_completed_session_from_disk_renders_results_and_viewer | 16.6 | |
| test_ui_apptest.py::test_sidebar_lists_completed_and_failed_sessions | 16.7 | Sessions tab, not a sidebar |
| test_ui_apptest.py::test_revision_history_expander_lists_revisions | 16.7 | |
| test_ui_apptest.py::test_revision_history_hidden_without_active_session | 16.7 | |
| test_ui_apptest.py::test_restore_revision_loads_its_params_and_marks_all_pending | 16.7 | |
| test_ui_apptest.py::test_redock_after_restore_plans_full_rerun | 16.7 | |
| test_ui_apptest.py::test_cancelled_redock_plan_is_offered_again | ported: test_nicegui_setup.py::test_cancelled_redock_plan_is_offered_again |  |
| test_ui_apptest.py::test_finished_run_clears_pending_only_when_completed | ported: test_nicegui_setup.py::test_finished_run_clears_pending_only_when_completed |  |
| test_ui_apptest.py::test_restore_disabled_while_job_runs | 16.7 | |
| test_ui_apptest.py::test_chat_undo_refused_while_job_runs | ported: test_nicegui_chat.py::test_chat_undo_refused_while_job_runs |  |
| test_ui_apptest.py::test_chat_show_history_and_undo | ported: test_nicegui_chat.py::test_chat_show_history_and_undo |  |
| test_ui_apptest.py::test_param_widgets_survive_early_rerun_in_chat_column | N/A | Streamlit drops widgets a rerun stops before drawing; NiceGUI draws each page once |
| test_ui_apptest.py::test_chat_history_without_active_session | ported: test_nicegui_chat.py::test_chat_history_without_active_session |  |
| test_ui_apptest.py::test_save_results_panel_writes_bundle | 16.6 | |
| test_ui_apptest.py::test_save_results_blocked_while_parameters_pending | 16.6 | |
| test_ui_apptest.py::test_save_results_blocked_when_parameters_never_ran_after_load | 16.6 | |
| test_ui_apptest.py::test_chat_save_refused_while_job_runs | ported: test_nicegui_chat.py::test_chat_save_refused_while_job_runs |  |
| test_ui_apptest.py::test_save_results_conflict_overwrite_and_new_name | 16.6 | |
| test_ui_apptest.py::test_chat_save_docking_as_name | ported: test_nicegui_chat.py::test_chat_save_docking_as_name |  |
| test_ui_apptest.py::test_file_downloads_exclude_saved_bundles | 16.6 | |
| test_ui_apptest.py::test_save_bundle_name_survives_early_rerun_in_chat_column | N/A | Streamlit drops widgets a rerun stops before drawing; NiceGUI draws each page once |
| test_ui_apptest.py::test_save_results_sits_between_viewer_and_session_log | 16.6 | re-specify the position for the Results tab layout |
| test_ui_apptest.py::test_receptor_load_with_box_fields_set_logs_no_session_state_warning | N/A | Streamlit's warning for a widget given both a default and a Session State value |
| test_ui_apptest.py::test_redock_after_load_of_never_run_restore_plans_full_rerun | 16.7 | |
| test_ui_apptest.py::test_redock_after_stopped_first_run_docks_again | ported: test_nicegui_setup.py::test_redock_after_stopped_first_run_docks_again |  |
| test_ui_apptest.py::test_chat_undo_of_state_only_revision_is_refused | ported: test_nicegui_chat.py::test_chat_undo_of_state_only_revision_is_refused |  |
| test_ui_apptest.py::test_restore_drops_box_picker_receptor_only_for_another_protein | 16.7 | |
| test_ui_apptest.py::test_finished_run_reply_shows_without_rerun | N/A | Streamlit needs the reply drawn in the same script run; NiceGUI pushes updates. The reply itself: test_nicegui_setup.py::test_finished_run_reply_is_rendered_in_chat |
| test_ui_apptest.py::test_chat_command_reply_shows_without_rerun | N/A | Streamlit needs the reply drawn in the same script run; NiceGUI pushes updates. The replies themselves: test_nicegui_chat.py |
| test_ui_apptest.py::test_run_docking_with_missing_params_replies_without_rerun | ported: test_nicegui_setup.py::test_run_docking_with_missing_params_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_redock_with_nothing_changed_replies_without_rerun | ported: test_nicegui_setup.py::test_redock_with_nothing_changed_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_save_session_replies_without_rerun | ported: test_nicegui_setup.py::test_save_session_replies_without_rerun | the no-rerun part is Streamlit-only |
| test_ui_apptest.py::test_save_session_saves_a_real_change_with_defaults | 16.7 | its second half needs Load (Sessions tab); the Save Session reply itself: test_save_session_replies_without_rerun |
| test_ui_apptest.py::test_save_session_with_form_back_at_checkpoint_clears_stale_draft | 16.7 | its second half needs Load (Sessions tab); the Save Session reply itself: test_save_session_replies_without_rerun |
| test_ui_apptest.py::test_typed_center_survives_after_a_viewer_click | ported: test_nicegui_box_picker.py::test_typed_center_survives_after_a_viewer_click | a NiceGUI click is an event, sent once; the typed center must still survive the redraws |
| test_ui_apptest.py::test_clicking_the_same_atom_again_after_typing_is_applied | ported: test_nicegui_box_picker.py::test_clicking_the_same_atom_again_after_typing_is_applied | no clickId needed: each click is its own event |
| test_ui_apptest.py::test_chat_parsed_box_moves_the_picker_box | ported: test_nicegui_box_picker.py::test_chat_parsed_box_moves_the_picker_box |  |
| test_ui_apptest.py::test_size_step_with_size_unset_draws_the_stepped_box | ported: test_nicegui_box_picker.py::test_size_step_with_size_unset_draws_the_stepped_box |  |
| test_ui_apptest.py::test_click_on_undrawable_box_docks_the_box_shown | ported: test_nicegui_box_picker.py::test_click_on_undrawable_box_docks_the_box_shown |  |
| test_ui_apptest.py::test_job_finishing_mid_run_is_handled_by_one_more_poll | ported: test_nicegui_setup.py::test_job_finishing_mid_run_is_handled_by_one_more_poll | the poll is the ui.timer |
| test_ui_apptest.py::test_cpu_field_shows_only_for_vina_and_bounds_workers | ported: test_nicegui_setup.py::test_cpu_field_shows_only_for_vina_and_bounds_workers |  |
| test_ui_apptest.py::test_switching_algorithm_redocks_dock_and_visualize_only | ported: test_nicegui_setup.py::test_switching_algorithm_redocks_dock_and_visualize_only |  |
| test_ui_apptest.py::test_pso_mode_sends_one_cpu_whatever_the_cpu_field_holds | ported: test_nicegui_setup.py::test_pso_mode_sends_one_cpu_whatever_the_cpu_field_holds |  |
| test_ui_apptest.py::test_loaded_pre_phase15_session_reads_as_moldina_with_nothing_to_redock | 16.7 | |
| test_ui_apptest.py::test_chat_parse_without_algorithm_keeps_the_selector | ported: test_nicegui_chat.py::test_chat_parse_without_algorithm_keeps_the_selector |  |
| test_ui_apptest.py::test_chat_parse_naming_an_unknown_algorithm_renders_without_crash | ported: test_nicegui_chat.py::test_chat_parse_naming_an_unknown_algorithm_renders_without_crash |  |
| test_ui_apptest.py::test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph | ported: test_nicegui_chat.py::test_chat_parse_keeps_the_users_vina_cpu_exhaustiveness_and_ph |  |
| test_ui_apptest.py::test_chat_algorithm_command_moves_the_selector_and_resets_workers | ported: test_nicegui_chat.py::test_chat_algorithm_command_moves_the_selector_and_resets_workers |  |
| test_ui_apptest.py::test_summary_shows_the_selected_algorithm | ported: test_nicegui_setup.py::test_summary_shows_the_selected_algorithm |  |
| test_ui_apptest.py::test_results_name_the_algorithm_that_docked | 16.6 | |
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
"""
import ast
import re
from collections import Counter
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# The Streamlit tier: every test file that needs streamlit to run.
STREAMLIT_TIER_FILES = (
    "test_ui_apptest.py",
    "test_ui_app_helpers.py",
    "test_ui_session_state.py",
    "test_ui_streamlit_adapter.py",
)

PENDING_STAGES = {"16.6", "16.7"}
_ROW = re.compile(r"^\| (?P<test>\S+) \| (?P<target>[^|]+?) \| (?P<note>[^|]*?) ?\|$")
_REFERENCE = re.compile(r"^(?P<kind>ported|core): (?P<file>test_\w+\.py)::(?P<name>test_\w+)$")


def _rows():
    rows = []
    for line in __doc__.splitlines():
        match = _ROW.match(line)
        if match and match["test"] != "Streamlit":
            rows.append((match["test"], match["target"].strip(), match["note"].strip()))
    return rows


def _test_names(filename):
    tree = ast.parse((TESTS_DIR / filename).read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }


def test_every_streamlit_tier_test_has_exactly_one_row():
    in_table = Counter(test for test, _, _ in _rows())
    assert [test for test, count in in_table.items() if count > 1] == []

    in_files = {f"{name}::{test}" for name in STREAMLIT_TIER_FILES for test in _test_names(name)}
    assert sorted(in_files - set(in_table)) == [], "Streamlit tests missing from the parity table"
    assert sorted(set(in_table) - in_files) == [], "parity rows for tests that no longer exist"


def test_streamlit_tier_file_list_is_complete():
    # A new file that needs streamlit belongs in STREAMLIT_TIER_FILES.
    needs_streamlit = {
        path.name for path in TESTS_DIR.glob("test_*.py")
        if path.name != Path(__file__).name  # this file names the marker below
        and 'importorskip("streamlit' in path.read_text(encoding="utf-8")
    }
    assert needs_streamlit == set(STREAMLIT_TIER_FILES)


@pytest.mark.parametrize("test, target, note", _rows(), ids=[row[0] for row in _rows()])
def test_row_target_is_valid(test, target, note):
    if target in PENDING_STAGES:
        return
    if target == "N/A":
        assert note, "an N/A row needs its reason"
        return
    reference = _REFERENCE.match(target)
    assert reference, f"unknown target {target!r}"
    assert reference["name"] in _test_names(reference["file"]), f"{target} doesn't exist"
