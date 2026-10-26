"""Session intent classification and parameter extraction."""

import pytest

from services.llm_orchestrator.workflow_llm_parser import (
    classify_session_intent,
    detect_docking_algorithm_command,
    detect_protein_geometry_command,
    detect_run_command,
    extract_updates_from_command_with_llm,
    split_command_sequence,
    _llm_split_command_sequence,
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
        ("undo rev 3", "undo_revision", "3"),
        ("Undo to revision #7", "undo_revision", "7"),
        ("undo", "undo_revision", None),
        # A number elsewhere in the sentence is not a revision (R1, Phase 13).
        ("undo the box change and set exhaustiveness to 16", "undo_revision", None),
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


# ---------------------------------------------------------------------------
# Multi-intent command sequencing (Phase 10) — deterministic splitter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("state and save results as trial_a", ["state", "save results as trial_a"]),
        ("show history then undo 14", ["show history", "undo 14"]),
        (
            "change ligand to HEM and rerun docking",
            ["change ligand to HEM", "rerun docking"],
        ),
        (
            "state and save results as trial_a and end session",
            ["state", "save results as trial_a", "end session"],
        ),
    ],
)
def test_split_command_sequence_valid_chains(text, expected):
    assert split_command_sequence(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "set center 5,6,7 and box 20,20,20",
        "state",
        "search ligand aspirin and vitamin c",
    ],
)
def test_split_command_sequence_no_split_when_tail_unrecognized(text):
    """A tail that doesn't independently match a known command must not be split off."""
    assert split_command_sequence(text) == [text]


# ---------------------------------------------------------------------------
# Multi-intent command sequencing (Phase 10) — LLM-assisted second pass
# ---------------------------------------------------------------------------

def test_llm_split_command_sequence_returns_validated_segments(llm_client):
    # Both proposed segments must classify deterministically (without another
    # llm_client.generate call) so the mocked split response above doesn't
    # leak into the per-segment validation pass.
    llm_client.generate.return_value = '{"segments": ["state", "save results as trial_a"]}'
    segments = _llm_split_command_sequence(llm_client, "state then save results as trial_a")
    assert segments == ["state", "save results as trial_a"]


def test_llm_split_command_sequence_falls_back_on_unknown_segment(llm_client):
    llm_client.generate.return_value = '{"segments": ["blarg florp zorp", "also nonsense"]}'
    text = "blarg florp zorp then also nonsense"
    assert _llm_split_command_sequence(llm_client, text) == [text]


@pytest.mark.parametrize(
    "llm_output",
    [
        "not json at all",
        '{"segments": "state"}',
        '{"segments": ["state"]}',
        '{"segments": ["state", ""]}',
        '{"other_key": ["state", "save results"]}',
    ],
)
def test_llm_split_command_sequence_falls_back_on_malformed_output(llm_client, llm_output):
    llm_client.generate.return_value = llm_output
    text = "state then save results"
    assert _llm_split_command_sequence(llm_client, text) == [text]


def test_llm_split_command_sequence_falls_back_when_words_dropped(llm_client):
    """Anti-hallucination guard: segments must not drop words from the original input."""
    llm_client.generate.return_value = '{"segments": ["state", "save"]}'
    text = "state then save results as trial_a"
    assert _llm_split_command_sequence(llm_client, text) == [text]


def test_llm_split_command_sequence_falls_back_on_exception(llm_client):
    llm_client.generate.side_effect = RuntimeError("LLM unavailable")
    text = "state then save results"
    assert _llm_split_command_sequence(llm_client, text) == [text]


# ---------------------------------------------------------------------------
# detect_protein_geometry_command -- Stage 12.2 ("set box center as protein
# center" / "look across all protein" chat commands). Pure regex, no LLM.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "set up box center as protein center",
        "set box center as protein center",
        "set box center to protein center",
        "use protein center",
        "use protein center for the box",
        "center the box on the protein",
        "center box on protein",
        "box center = protein center",
        "Protein Center",
    ],
)
def test_detect_protein_geometry_command_center(command):
    assert detect_protein_geometry_command(command) == "center"


@pytest.mark.parametrize(
    "command",
    [
        "look across all protein",
        "look across the whole protein",
        "look across entire protein",
        "cover the whole protein",
        "cover entire protein",
        "box size to cover the whole protein",
        "whole protein box",
        "box size for the entire protein",
        "Look Across All Protein",
    ],
)
def test_detect_protein_geometry_command_box_size(command):
    assert detect_protein_geometry_command(command) == "box_size"


@pytest.mark.parametrize(
    "command",
    [
        "set center to 10,20,30",
        "set box size to 25,30,35",
        "change ligand to HEM",
        "use pymol box",
        "use pymol selection sele",
        "rerun",
        "dock",
        "save results as trial_a",
        "set protein_id to 6lu7",
        "undo 5",
        # Review Stage R1 (Phase 12): these mention "protein" near a box/
        # cover/whole keyword but qualify it with a specific sub-region --
        # must NOT be silently routed to a full-protein blind-docking box.
        "cover the whole protein pocket",
        "widen the box to cover the whole protein active site",
        "resize the box, it should cover the whole protein binding region",
        "widen the box a bit to cover more of the protein around the pocket",
    ],
)
def test_detect_protein_geometry_command_no_false_positives(command):
    assert detect_protein_geometry_command(command) is None


@pytest.mark.parametrize(
    "command,expected_target",
    [
        ("set up box center as protein center", "center"),
        ("look across all protein", "box_size"),
    ],
)
def test_geometry_phrases_classify_as_set_protein_geometry(llm_client, command, expected_target):
    """Stage 12.3: _deterministic_intent() (via classify_session_intent())
    now recognizes both example commands deterministically -- no LLM call,
    and the intent's "name" carries which field (center/box_size) to set,
    consumed by nlp_orchestrator.py::_handle_set_protein_geometry()."""
    result = classify_session_intent(llm_client, command)
    assert result["intent"] == "set_protein_geometry"
    assert result["name"] == expected_target
    llm_client.generate.assert_not_called()


def test_update_parameters_catchall_still_wins_for_non_geometry_set_commands(llm_client):
    """Regression guard for Critical Finding #2: an ordinary 'set ...'
    parameter command must still classify as update_parameters, not get
    swallowed by the new geometry check placed just before it."""
    result = classify_session_intent(llm_client, "set center to 10,20,30")
    assert result["intent"] == "update_parameters"
    llm_client.generate.assert_not_called()


# ---------------------------------------------------------------------------
# detect_docking_algorithm_command -- Stage 15.2 ("use vina" / "dock with
# vina" chat commands). Pure regex, no LLM; checked before the rerun prefix
# (Phase 15 finding 6).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command,algorithm,rerun",
    [
        ("use vina", "vina", False),
        ("Use Vina.", "vina", False),
        ("use autodock vina", "vina", False),
        ("use AutoDock-Vina", "vina", False),
        ("use monte carlo", "vina", False),
        ("use the vina algorithm", "vina", False),
        ("switch to vina (monte carlo)", "vina", False),
        ("change to PSO", "moldina", False),
        ("switch to moldina", "moldina", False),
        ("use moldina (pso)", "moldina", False),
        ("use moldina pso search", "moldina", False),
        ("set docking_algorithm vina", "vina", False),
        ("set docking_algorithm=vina", "vina", False),
        ("set docking_algorithm = moldina", "moldina", False),
        ("set docking algorithm to vina", "vina", False),
        ("set the algorithm to PSO", "moldina", False),
        ("change the docking algorithm to monte carlo", "vina", False),
        ("switch algorithm to vina", "vina", False),
        ("algorithm: vina", "vina", False),
        ("docking algorithm moldina", "moldina", False),
        ("please use vina", "vina", False),
        ("dock with vina", "vina", True),
        ("Dock using AutoDock Vina!", "vina", True),
        ("redock with vina", "vina", True),
        ("re-dock with moldina", "moldina", True),
        ("rerun with pso", "moldina", True),
        ("rerun docking with vina", "vina", True),
        ("run it again with vina", "vina", True),
        ("dock again using moldina", "moldina", True),
    ],
)
def test_detect_docking_algorithm_command(command, algorithm, rerun):
    assert detect_docking_algorithm_command(command) == {"algorithm": algorithm, "rerun": rerun}


@pytest.mark.parametrize(
    "command",
    [
        "dock",
        "rerun",
        "rerun docking",
        "dock 6lu7 with ATP",
        "dock with ATP",
        "use two ATP ligands",
        "set center to 10,20,30",
        "use pymol box",
        "use protein center",
        "search vina",
        "dock with vina and visualize",
        "new dock 6lu7 with vina",
        "Download 6lu7 and ATP, prepare and dock with vina",
        "what is vina?",
        "use vina for ligand HEM",
        "set docking_algorithm gnina",
        "use gnina",
        "vinardo",
    ],
)
def test_detect_docking_algorithm_command_no_false_positives(command):
    assert detect_docking_algorithm_command(command) is None


# ---------------------------------------------------------------------------
# detect_run_command -- Stage 17.1 (the GUI chat's Run Docking)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "run", "Run docking.", "start docking", "start the docking now", "redock", "re-dock",
        "rerun", "dock", "dock it again", "go", "go ahead!", "please run", "ok, run it",
        "launch the job", "execute the workflow",
    ],
)
def test_detect_run_command(command):
    assert detect_run_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "dock ATP into 6lu7", "run 6lu7", "start a new session", "dock with vina",
        "run show history", "rerun the docking with ATP", "dock with vina and visualize",
        "how do I run docking?", "don't run", "running", "show history",
    ],
)
def test_detect_run_command_no_false_positives(command):
    assert detect_run_command(command) is False


@pytest.mark.parametrize(
    "command,algorithm,rerun",
    [
        ("dock with vina", "vina", True),
        ("use vina", "vina", False),
        ("set docking_algorithm vina", "vina", False),
        ("switch to PSO", "moldina", False),
    ],
)
def test_algorithm_phrases_classify_as_set_docking_algorithm(llm_client, command, algorithm, rerun):
    """"dock with vina" must switch, not hit the rerun prefix; "set
    docking_algorithm vina" must not fall to the LLM update path."""
    result = classify_session_intent(llm_client, command)
    assert result["intent"] == "set_docking_algorithm"
    assert result["name"] == algorithm
    assert result["rerun"] is rerun
    llm_client.generate.assert_not_called()


@pytest.mark.parametrize("command", ["dock", "rerun docking", "run", "dock 6lu7 with ATP"])
def test_plain_dock_commands_still_rerun(llm_client, command):
    assert classify_session_intent(llm_client, command)["intent"] == "rerun_steps"
    llm_client.generate.assert_not_called()


def test_algorithm_switch_chains_with_rerun():
    assert split_command_sequence("use vina and rerun") == ["use vina", "rerun"]
    assert split_command_sequence("change ligand to HEM and use vina") == ["change ligand to HEM", "use vina"]
