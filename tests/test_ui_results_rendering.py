"""Tests for the results-rendering helpers (pure functions only)."""
import pytest

from ui.results import prepare_affinity_data, make_affinity_chart_figure


def _r(ligand_id: str, best_affinity):
    return {"ligand_id": ligand_id, "best_affinity": best_affinity}


class TestPrepareAffinityData:
    def test_returns_none_for_empty_list(self):
        assert prepare_affinity_data([]) is None

    def test_returns_none_when_all_affinities_are_none(self):
        assert prepare_affinity_data([_r("ATP", None), _r("HEM", None)]) is None

    def test_skips_none_entries_keeps_valid(self):
        result = prepare_affinity_data([_r("ATP", -8.4), _r("HEM", None)])
        assert result == [("ATP", -8.4)]

    def test_sorts_most_negative_first(self):
        data = prepare_affinity_data([
            _r("ADP", -6.5),
            _r("ATP", -8.4),
            _r("HEM", -7.1),
        ])
        labels = [label for label, _ in data]
        values = [v for _, v in data]
        assert labels == ["ATP", "HEM", "ADP"]
        assert values == sorted(values)

    def test_single_entry(self):
        result = prepare_affinity_data([_r("ATP", -8.4)])
        assert result == [("ATP", -8.4)]


class TestMakeAffinityChartFigure:
    def test_returns_none_for_empty_list(self):
        assert make_affinity_chart_figure([]) is None

    def test_returns_none_when_all_affinities_none(self):
        assert make_affinity_chart_figure([_r("ATP", None)]) is None

    def test_returns_figure_and_height_scales(self):
        go = pytest.importorskip("plotly.graph_objects")
        dock = [_r(f"L{i}", float(-i)) for i in range(1, 6)]
        fig = make_affinity_chart_figure(dock)
        assert fig is not None
        assert isinstance(fig, go.Figure)
        assert fig.layout.height >= 200
