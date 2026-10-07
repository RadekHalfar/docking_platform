"""Tests for common/docking_algorithm.py (Phase 15, Stage 15.1)."""

import pytest

from common.docking_algorithm import (
    DEFAULT_DOCKING_ALGORITHM,
    DOCKING_ALGORITHMS,
    cpus_per_ligand,
    docking_algorithm_label,
    docking_algorithm_of,
    normalize_docking_algorithm,
)


def test_default_is_moldina_and_allowed():
    assert DEFAULT_DOCKING_ALGORITHM == "moldina"
    assert DEFAULT_DOCKING_ALGORITHM in DOCKING_ALGORITHMS


@pytest.mark.parametrize(
    "value,expected",
    [
        ("moldina", "moldina"),
        ("Moldina", "moldina"),
        ("PSO", "moldina"),
        ("Moldina (PSO)", "moldina"),
        ("vina", "vina"),
        ("  VINA ", "vina"),
        ("AutoDock Vina", "vina"),
        ("autodock_vina", "vina"),
        ("Monte Carlo", "vina"),
        ("monte-carlo", "vina"),
        ("Vina (Monte Carlo)", "vina"),
        ("(vina)", "vina"),
        ("moldina(pso)", "moldina"),
    ],
)
def test_aliases_normalise(value, expected):
    assert normalize_docking_algorithm(value) == expected


@pytest.mark.parametrize("value", ["", "gnina", "smina", "vina 1.2", None, 1, ["vina"]])
def test_unrecognised_values_return_none(value):
    assert normalize_docking_algorithm(value) is None


def test_label_names_known_algorithms_and_shows_unknown_as_is():
    assert docking_algorithm_label("vina") == "Vina (Monte Carlo)"
    assert docking_algorithm_label("gnina") == "gnina"
    assert docking_algorithm_label(None) == "None"


@pytest.mark.parametrize("dock_result,expected", [
    ({"docking_algorithm": "vina"}, "vina"),
    ({"successful": 1}, "moldina"),  # written before Phase 15
    ({"docking_algorithm": None}, "moldina"),
    (None, "moldina"),
])
def test_docking_algorithm_of_a_result(dock_result, expected):
    assert docking_algorithm_of(dock_result) == expected


@pytest.mark.parametrize("algorithm,cpu,expected", [
    ("vina", 4, 4), ("vina", 0, 1), ("moldina", 4, 1), ("moldina", 1, 1),
])
def test_cpus_per_ligand_counts_cpu_only_for_vina(algorithm, cpu, expected):
    assert cpus_per_ligand(algorithm, cpu) == expected
