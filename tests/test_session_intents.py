"""Session intent classification and parameter extraction."""
from unittest.mock import Mock

from services.llm_orchestrator.workflow_llm_parser import (
    classify_session_intent,
    extract_updates_from_command_with_llm,
)


# ---------------------------------------------------------------------------
# Deterministic fallbacks — LLM must NOT be called
# ---------------------------------------------------------------------------

def test_classify_show_history_deterministic():
    """'show history' must resolve deterministically as show_history."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "show history")
    assert result["intent"] == "show_history"
    llm_client.generate.assert_not_called()


def test_classify_end_session_deterministic():
    """'end session' must resolve deterministically as end_session."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "end session")
    assert result["intent"] == "end_session"
    llm_client.generate.assert_not_called()


def test_classify_save_results_with_name_deterministic():
    """'save results as hem_box_a' must resolve deterministically with the correct name."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "save results as hem_box_a")
    assert result["intent"] == "save_results"
    assert result["name"] == "hem_box_a"
    llm_client.generate.assert_not_called()


def test_classify_save_results_without_name_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "save results")
    assert result["intent"] == "save_results"
    assert result["name"] is None
    llm_client.generate.assert_not_called()


def test_classify_show_state_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "state")
    assert result["intent"] == "show_state"
    llm_client.generate.assert_not_called()


def test_classify_undo_with_revision_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "undo 14")
    assert result["intent"] == "undo_revision"
    assert result["name"] == "14"
    llm_client.generate.assert_not_called()


def test_classify_new_workflow_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "new Download 6lu7 and dock ATP")
    assert result["intent"] == "new_workflow"
    llm_client.generate.assert_not_called()


def test_classify_rerun_deterministic():
    """'rerun' must resolve deterministically as rerun_steps."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "rerun")
    assert result["intent"] == "rerun_steps"
    llm_client.generate.assert_not_called()


def test_classify_rerun_docking_deterministic():
    """'rerun docking' must resolve deterministically as rerun_steps."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "rerun docking")
    assert result["intent"] == "rerun_steps"
    llm_client.generate.assert_not_called()


def test_classify_history_alias_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "history")
    assert result["intent"] == "show_history"
    llm_client.generate.assert_not_called()


def test_classify_quit_session_deterministic():
    llm_client = Mock()
    result = classify_session_intent(llm_client, "quit session")
    assert result["intent"] == "end_session"
    llm_client.generate.assert_not_called()


# ---------------------------------------------------------------------------
# LLM-based classification (deterministic fallback returns None)
# ---------------------------------------------------------------------------

def test_deterministic_classify_change_ligand_to_hem():
    """'change ligand to HEM' must classify as update_parameters deterministically (no LLM call)."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "change ligand to HEM")
    assert result["intent"] == "update_parameters"
    llm_client.generate.assert_not_called()


def test_deterministic_classify_use_pymol_box():
    """'use pymol selection sele' must classify as use_pymol_box deterministically."""
    llm_client = Mock()
    result = classify_session_intent(llm_client, "use pymol selection sele")
    assert result["intent"] == "use_pymol_box"
    llm_client.generate.assert_not_called()


def test_llm_classify_unknown_on_bad_json():
    llm_client = Mock()
    llm_client.generate.return_value = "not json at all"
    result = classify_session_intent(llm_client, "blarg florp zorp")
    assert result["intent"] == "unknown"


def test_llm_classify_unknown_on_unrecognized_intent_value():
    llm_client = Mock()
    llm_client.generate.return_value = '{"intent": "explode_everything", "name": null}'
    result = classify_session_intent(llm_client, "do something strange")
    assert result["intent"] == "unknown"


def test_llm_classify_falls_back_to_unknown_on_exception():
    llm_client = Mock()
    llm_client.generate.side_effect = RuntimeError("LLM unavailable")
    result = classify_session_intent(llm_client, "anything")
    assert result["intent"] == "unknown"


# ---------------------------------------------------------------------------
# Parameter extraction from natural language command
# ---------------------------------------------------------------------------

def test_extract_updates_ligand_change():
    llm_client = Mock()
    llm_client.generate.return_value = '{"parameter_updates": {"ligand_ids": ["HEM"]}}'
    updates = extract_updates_from_command_with_llm(llm_client, "change ligand to HEM")
    assert updates == {"ligand_ids": ["HEM"]}


def test_extract_updates_center():
    llm_client = Mock()
    llm_client.generate.return_value = '{"parameter_updates": {"center": [10.0, 20.0, 30.0]}}'
    updates = extract_updates_from_command_with_llm(llm_client, "set center to 10,20,30")
    assert updates == {"center": [10.0, 20.0, 30.0]}


def test_extract_updates_multiple_fields():
    llm_client = Mock()
    llm_client.generate.return_value = (
        '{"parameter_updates": {"center": [5.0, 6.0, 7.0], "box_size": [20.0, 20.0, 20.0]}}'
    )
    updates = extract_updates_from_command_with_llm(llm_client, "set center 5,6,7 and box 20,20,20")
    assert updates["center"] == [5.0, 6.0, 7.0]
    assert updates["box_size"] == [20.0, 20.0, 20.0]


def test_extract_updates_empty_on_bad_json():
    llm_client = Mock()
    llm_client.generate.return_value = "not json"
    updates = extract_updates_from_command_with_llm(llm_client, "something vague")
    assert updates == {}


def test_extract_updates_empty_on_exception():
    llm_client = Mock()
    llm_client.generate.side_effect = RuntimeError("LLM error")
    updates = extract_updates_from_command_with_llm(llm_client, "something")
    assert updates == {}
