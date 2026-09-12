"""Tests for InteractiveSession helpers introduced/used by Stage 11.2 (GUI redock wiring)."""

from __future__ import annotations

from services.llm_orchestrator.interactive_session import compute_changed_params


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
