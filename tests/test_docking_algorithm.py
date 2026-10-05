"""Tests for common/docking_algorithm.py (Phase 15, Stage 15.1)."""

import pytest

from common.docking_algorithm import (
    DEFAULT_DOCKING_ALGORITHM,
    DOCKING_ALGORITHMS,
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
    ],
)
def test_aliases_normalise(value, expected):
    assert normalize_docking_algorithm(value) == expected


@pytest.mark.parametrize("value", ["", "gnina", "smina", "vina 1.2", None, 1, ["vina"]])
def test_unrecognised_values_return_none(value):
    assert normalize_docking_algorithm(value) is None
