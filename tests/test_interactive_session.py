"""Tests for InteractiveSession helpers introduced/used by Stage 11.2 (GUI redock wiring)."""

from __future__ import annotations

from services.llm_orchestrator.interactive_session import (
    compute_changed_params,
    resolve_prepared_protein_pdb_path,
)


def test_no_prior_params_reports_every_key_as_changed():
    new = {"protein_id": "6lu7", "ligand_ids": ["ATP"]}
    assert compute_changed_params(None, new) == {"protein_id", "ligand_ids"}


def test_identical_params_report_no_changes():
    old = {"protein_id": "6lu7", "ligand_ids": ["ATP"]}
    new = dict(old)
    assert compute_changed_params(old, new) == set()


def test_only_differing_keys_are_reported():
    old = {"protein_id": "6lu7", "ligand_ids": ["ATP"], "center": [1, 2, 3]}
    new = {"protein_id": "6lu7", "ligand_ids": ["HEM"], "center": [1, 2, 3]}
    assert compute_changed_params(old, new) == {"ligand_ids"}


def test_key_added_or_removed_counts_as_changed():
    old = {"protein_id": "6lu7"}
    new = {"protein_id": "6lu7", "box_size": [20, 20, 20]}
    assert compute_changed_params(old, new) == {"box_size"}


def test_returns_a_set_not_a_list():
    result = compute_changed_params({}, {"protein_id": "6lu7"})
    assert isinstance(result, set)


# ---------------------------------------------------------------------------
# resolve_prepared_protein_pdb_path -- Stage 12.2 (protein geometry auto-fill)
# ---------------------------------------------------------------------------


def test_resolve_pdb_path_none_last_results():
    assert resolve_prepared_protein_pdb_path(None) is None


def test_resolve_pdb_path_empty_dict():
    assert resolve_prepared_protein_pdb_path({}) is None


def test_resolve_pdb_path_no_prepare_protein_step():
    last_results = {"results": {"get_input_files": {}}}
    assert resolve_prepared_protein_pdb_path(last_results) is None


def test_resolve_pdb_path_empty_prepared_proteins_list():
    last_results = {"results": {"prepare_protein": {"prepared_proteins": []}}}
    assert resolve_prepared_protein_pdb_path(last_results) is None


def test_resolve_pdb_path_missing_pdb_path_key():
    last_results = {
        "results": {"prepare_protein": {"prepared_proteins": [{"pdbqt_path": "/x/6lu7.pdbqt"}]}}
    }
    assert resolve_prepared_protein_pdb_path(last_results) is None


def test_resolve_pdb_path_happy_path():
    last_results = {
        "results": {
            "prepare_protein": {
                "prepared_proteins": [
                    {"pdb_path": "/app/orchestrator_output/job_1/prepared/6lu7_prepared.pdb"}
                ]
            }
        }
    }
    assert (
        resolve_prepared_protein_pdb_path(last_results)
        == "/app/orchestrator_output/job_1/prepared/6lu7_prepared.pdb"
    )
