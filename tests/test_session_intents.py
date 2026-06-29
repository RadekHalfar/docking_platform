"""Session intent classification and parameter extraction."""

import pytest

from services.llm_orchestrator.workflow_llm_parser import (
    classify_session_intent,
    extract_updates_from_command_with_llm,
)


# ---------------------------------------------------------------------------
# Deterministic fallbacks — LLM must NOT be called
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command,expected_intent,expected_name",
    [
        ("show history", "show_history", None),
        ("history", "show_history", None),
        ("end session", "end_session", None),
        ("quit session", "end_session", None),
        ("save results as hem_box_a", "save_results", "hem_box_a"),
        ("save results", "save_results", None),
        ("state", "show_state", None),
        ("undo 14", "undo_revision", "14"),
        ("new Download 6lu7 and dock ATP", "new_workflow", None),
        ("rerun", "rerun_steps", None),
        ("rerun docking", "rerun_steps", None),
        ("search ligand aspirin", "search_ligand", "aspirin"),
        ("find ligand named ibuprofen", "search_ligand", "ibuprofen"),
    ],
)
def test_classify_deterministic_intents(llm_client, command, expected_intent, expected_name):
    result = classify_session_intent(llm_client, command)
    assert result["intent"] == expected_intent
    assert result["name"] == expected_name
    llm_client.generate.assert_not_called()


# ---------------------------------------------------------------------------
# LLM-based classification (deterministic fallback returns None)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command,expected_intent",
    [
        ("change ligand to HEM", "update_parameters"),
        ("use pymol selection sele", "use_pymol_box"),
    ],
)
def test_deterministic_classify_parameter_operations(llm_client, command, expected_intent):
    result = classify_session_intent(llm_client, command)
    assert result["intent"] == expected_intent
    llm_client.generate.assert_not_called()


@pytest.mark.parametrize(
    "llm_output,user_command",
    [
        ("not json at all", "blarg florp zorp"),
        ('{"intent": "explode_everything", "name": null}', "do something strange"),
    ],
)
def test_llm_classify_unknown_on_invalid_llm_output(llm_client, llm_output, user_command):
    llm_client.generate.return_value = llm_output
    result = classify_session_intent(llm_client, user_command)
    assert result["intent"] == "unknown"


def test_llm_classify_falls_back_to_unknown_on_exception(llm_client):
    llm_client.generate.side_effect = RuntimeError("LLM unavailable")
    result = classify_session_intent(llm_client, "anything")
    assert result["intent"] == "unknown"


# ---------------------------------------------------------------------------
# Parameter extraction from natural language command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "llm_output,user_command,expected_updates",
    [
        ('{"parameter_updates": {"ligand_ids": ["HEM"]}}', "change ligand to HEM", {"ligand_ids": ["HEM"]}),
        ('{"parameter_updates": {"center": [10.0, 20.0, 30.0]}}', "set center to 10,20,30", {"center": [10.0, 20.0, 30.0]}),
        (
            '{"parameter_updates": {"center": [5.0, 6.0, 7.0], "box_size": [20.0, 20.0, 20.0]}}',
            "set center 5,6,7 and box 20,20,20",
            {"center": [5.0, 6.0, 7.0], "box_size": [20.0, 20.0, 20.0]},
        ),
    ],
)
def test_extract_updates_success_cases(llm_client, llm_output, user_command, expected_updates):
    llm_client.generate.return_value = llm_output
    updates = extract_updates_from_command_with_llm(llm_client, user_command)
    assert updates == expected_updates


@pytest.mark.parametrize(
    "command,expected_scope",
    [
        ("save visualization my_run", "visualization"),
        ("save viz quick", "visualization"),
        ("save docking v1", "docking"),
        ("save results as final", "all"),
        ("save as control", "all"),
    ],
)
def test_save_scope_mapping(llm_client, command, expected_scope):
    """Save-results commands should carry the correct scope field (L6 dict-lookup refactor)."""
    result = classify_session_intent(llm_client, command)
    assert result["intent"] == "save_results"
    assert result["scope"] == expected_scope
    llm_client.generate.assert_not_called()


@pytest.mark.parametrize("user_command", ["something vague", "something"])
def test_extract_updates_empty_on_bad_llm_output_or_exception(llm_client, user_command):
    if user_command == "something vague":
        llm_client.generate.return_value = "not json"
    else:
        llm_client.generate.side_effect = RuntimeError("LLM error")

    updates = extract_updates_from_command_with_llm(llm_client, user_command)
    assert updates == {}
