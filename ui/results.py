"""Pure data-processing helpers for the results panel (no Streamlit dependency)."""
from __future__ import annotations

import html as _html
from typing import Any, Dict

from common.docking_algorithm import docking_algorithm_label


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


def build_comparison_dataframe(comparison: Dict[str, Any]):
    """Build a ligand x job affinity table from job_comparator.compare_jobs() output.

    Requires pandas — only available in the UI image, not the base image.
    """
    import pandas as pd

    return pd.DataFrame(
        comparison["affinities"],
        index=comparison["ligands"],
        columns=comparison["jobs"],
    )


def build_comparison_html(comparison: Dict[str, Any]) -> str:
    """Render a ligand x job affinity table as a self-contained HTML string.

    Built as raw HTML/inline-CSS rather than a pandas Styler passed to
    st.dataframe: Streamlit's interactive dataframe grid draws on a <canvas>
    and does not reliably apply Styler background colors across the
    light/dark theme toggle — it was rendering highlighted cells as black
    text on a black background in dark mode. Plain st.markdown content
    follows normal DOM/CSS and inherits the app's theme correctly, so only
    the highlighted "best" cells need an explicit (theme-independent)
    background + text color; all other cells are left unstyled and inherit
    the correct default color for whichever theme is active.
    """
    df = build_comparison_dataframe(comparison)
    best_per_ligand = comparison.get("best_per_ligand", {})

    border = "border-bottom:1px solid rgba(128,128,128,0.4);"
    header_cells = "".join(
        f"<th style='text-align:left;padding:4px 10px;{border}'>{_html.escape(str(col))}</th>"
        for col in df.columns
    )
    rows = [
        f"<tr><th style='text-align:left;padding:4px 10px;{border}'>Ligand</th>{header_cells}</tr>"
    ]
    # One algorithm per job column, so a row rather than a column; absent
    # from comparison dicts built before Phase 15.
    algorithms = comparison.get("algorithms")
    if algorithms:
        algorithm_cells = "".join(
            f"<td style='padding:4px 10px;font-style:italic;{border}'>"
            f"{_html.escape(docking_algorithm_label(alg))}</td>"
            for alg in algorithms
        )
        rows.append(
            f"<tr><th style='text-align:left;padding:4px 10px;{border}'>Algorithm</th>{algorithm_cells}</tr>"
        )

    for ligand in df.index:
        best_job = best_per_ligand.get(ligand, (None, None))[0]
        cells = [f"<td style='padding:4px 10px;font-weight:600;{border}'>{_html.escape(str(ligand))}</td>"]
        for job in df.columns:
            val = df.loc[ligand, job]
            is_valid = val is not None and val == val  # excludes None and NaN
            text = f"{val:.2f}" if is_valid else "N/A"
            style = f"padding:4px 10px;{border}"
            if is_valid and job == best_job:
                style += "background-color:#90ee90;color:#111;font-weight:700;"
            cells.append(f"<td style='{style}'>{_html.escape(text)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        "<div style='overflow-x:auto;'>"
        "<table style='border-collapse:collapse;width:100%;font-size:0.9rem;'>"
        f"{''.join(rows)}"
        "</table></div>"
    )
