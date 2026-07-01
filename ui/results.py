"""Pure data-processing helpers for the results panel (no Streamlit dependency)."""
from __future__ import annotations


def prepare_affinity_data(dock_results: list) -> list[tuple[str, float]] | None:
    """Extract and sort (ligand_id, affinity) pairs from dock results.

    Returns None when there are no entries with a valid numeric affinity.
    Sorted most-negative (best binding) first.
    """
    valid = [
        (r["ligand_id"], r["best_affinity"])
        for r in dock_results
        if r.get("best_affinity") is not None
    ]
    if not valid:
        return None
    return sorted(valid, key=lambda x: x[1])


def make_affinity_chart_figure(dock_results: list):
    """Build a Plotly Figure from dock results. Returns None if no valid data.

    Requires plotly — only available in the UI image, not the base image.
    """
    data = prepare_affinity_data(dock_results)
    if data is None:
        return None

    import plotly.graph_objects as go

    labels, values = zip(*data)
    fig = go.Figure(
        go.Bar(
            x=list(values),
            y=list(labels),
            orientation="h",
            marker_color="steelblue",
        )
    )
    fig.update_layout(
        title="Binding Affinity (kcal/mol)",
        xaxis_title="Affinity (kcal/mol)",
        height=max(200, len(labels) * 40 + 80),
        margin=dict(l=10, r=10, t=40, b=40),
    )
    return fig
