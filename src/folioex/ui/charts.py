"""Plotly figures, styled from the theme tokens. One axis per chart, thin marks, recessive grid."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import plotly.graph_objects as go

from folioex.analytics.allocation import Slice
from folioex.analytics.compare import Line
from folioex.analytics.holdings import HoldingRow
from folioex.formatting import money, percent

_TOP_SLICES = 7  # more than this folds into "Other" so colours stay distinguishable
# Every figure carries its own height: containers then size to the chart, and a redraw (e.g. a theme
# switch) can never collapse it to the height of an empty container.
TREEMAP_HEIGHT, DONUT_HEIGHT, SIZES_HEIGHT = 380, 250, 180
ROW_HEIGHT, MIN_BARS_HEIGHT = 30, 240


def mix(a: str, b: str, k: float) -> str:
    """Blend two hex colours: k=0 → a, k=1 → b."""
    ra, ga, ba = (int(a[i : i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i : i + 2], 16) for i in (1, 3, 5))
    return f"#{round(ra + (rb - ra) * k):02x}{round(ga + (gb - ga) * k):02x}{round(ba + (bb - ba) * k):02x}"


def gain_color(gain: float | None, colors: dict[str, str]) -> str:
    if gain is None:
        return colors["surface-2"]
    if gain >= 0:
        return mix(colors["surface-2"], colors["up"], min(0.62, 0.12 + gain * 0.9))
    return mix(colors["surface-2"], colors["down"], min(0.62, 0.12 - gain * 1.4))


def base_layout(colors: dict[str, str], **extra: Any) -> dict[str, Any]:
    axis = {
        "gridcolor": colors["line"],
        "linecolor": colors["line-strong"],
        "zeroline": False,
        "tickfont": {"color": colors["muted"], "size": 11},
        "automargin": True,
    }
    layout: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {
            "family": "Public Sans, Segoe UI, system-ui, sans-serif",
            "color": colors["fg-2"],
            "size": 12,
        },
        "margin": {"l": 8, "r": 12, "t": 8, "b": 8},
        "hoverlabel": {
            "bgcolor": colors["surface"],
            "bordercolor": colors["line-strong"],
            "font": {"color": colors["fg"]},
        },
        "showlegend": False,
        "xaxis": dict(axis),
        "yaxis": dict(axis),
    }
    for key, value in extra.items():
        if key in ("xaxis", "yaxis") and isinstance(value, dict):
            layout[key] = {**axis, **value}
        else:
            layout[key] = value
    return layout


def treemap(rows: Sequence[HoldingRow], sectors: Sequence[Slice], colors: dict[str, str]) -> go.Figure:
    ids, labels, parents, values, fills, texts = [], [], [], [], [], []
    for s in sectors:
        ids.append(f"sector:{s.name}")
        labels.append(s.name)
        parents.append("")
        values.append(s.value)
        fills.append(colors["surface-2"])
        texts.append(percent(s.weight))
    for r in rows:
        ids.append(f"stock:{r.symbol}")
        labels.append(r.symbol)
        parents.append(f"sector:{r.sector}")
        values.append(r.value)
        fills.append(gain_color(r.gain_pct, colors))
        texts.append(percent(r.gain_pct, decimals=1, sign=True))
    fig = go.Figure(
        go.Treemap(
            ids=ids,
            labels=labels,
            parents=parents,
            values=values,
            text=texts,
            branchvalues="total",
            marker={"colors": fills, "line": {"color": colors["surface"], "width": 2}},
            texttemplate="<b>%{label}</b><br>%{text}",
            textfont={"color": colors["fg"], "size": 13},
            hovertemplate="<b>%{label}</b><br>%{value:$,.0f}<br>%{text}<extra></extra>",
            pathbar={"visible": False},
            tiling={"pad": 2},
        )
    )
    fig.update_layout(**base_layout(colors, margin={"l": 0, "r": 0, "t": 0, "b": 0}))
    fig.update_layout(height=TREEMAP_HEIGHT, autosize=True)
    return fig


def sector_donut(sectors: Sequence[Slice], colors: dict[str, str]) -> go.Figure:
    top = list(sectors[:_TOP_SLICES])
    rest = sectors[_TOP_SLICES:]
    names = [s.name for s in top] + (["Other"] if rest else [])
    values = [s.value for s in top] + ([sum(s.value for s in rest)] if rest else [])
    fills = [colors[f"s{i + 1}"] for i in range(len(top))] + ([colors["muted"]] if rest else [])
    fig = go.Figure(
        go.Pie(
            labels=names,
            values=values,
            hole=0.62,
            sort=False,
            direction="clockwise",
            marker={"colors": fills, "line": {"color": colors["surface"], "width": 2}},
            textinfo="percent",
            textposition="inside",
            insidetextfont={"color": "#ffffff", "size": 11},
            hovertemplate="%{label}<br>%{value:$,.0f} · %{percent}<extra></extra>",
        )
    )
    fig.update_layout(
        **base_layout(
            colors,
            showlegend=True,
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            legend={"font": {"size": 11, "color": colors["fg-2"]}, "x": 1, "y": 0.5, "xanchor": "left"},
        )
    )
    fig.update_layout(height=DONUT_HEIGHT, autosize=True)
    return fig


def size_bars(sizes: Sequence[Slice], colors: dict[str, str]) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            orientation="h",
            y=[s.name for s in sizes],
            x=[s.weight for s in sizes],
            marker={"color": colors["s1"]},
            text=[percent(s.weight) for s in sizes],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": colors["fg-2"]},
            hovertemplate="%{y}: %{x:.0%}<extra></extra>",
        )
    )
    fig.update_layout(
        **base_layout(
            colors,
            bargap=0.45,
            xaxis={"visible": False, "range": [0, 1.18]},
            yaxis={
                "autorange": "reversed",
                "gridcolor": "rgba(0,0,0,0)",
                "tickfont": {"color": colors["fg-2"]},
            },
        )
    )
    fig.update_layout(height=SIZES_HEIGHT, autosize=True)
    return fig


def gain_bars(rows: Sequence[HoldingRow], colors: dict[str, str]) -> go.Figure:
    ordered = sorted(rows, key=lambda r: r.gain)
    fig = go.Figure(
        go.Bar(
            orientation="h",
            y=[r.symbol for r in ordered],
            x=[r.gain for r in ordered],
            marker={"color": [colors["up"] if r.gain >= 0 else colors["down"] for r in ordered]},
            text=[money(r.gain, decimals=0, sign=True) for r in ordered],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": colors["fg-2"], "size": 11},
            hovertemplate="%{y}: %{x:$,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        **base_layout(
            colors,
            bargap=0.35,
            margin={"l": 8, "r": 64, "t": 8, "b": 8},
            xaxis={
                "tickprefix": "$",
                "tickformat": ",.0f",
                "zeroline": True,
                "zerolinecolor": colors["line-strong"],
            },
            yaxis={"gridcolor": "rgba(0,0,0,0)", "tickfont": {"color": colors["fg-2"]}},
        )
    )
    fig.update_layout(height=max(MIN_BARS_HEIGHT, ROW_HEIGHT * len(rows) + 40), autosize=True)
    return fig


def sparkline(values: Sequence[float], color: str, width: int = 76, height: int = 24) -> str:
    """A tiny line as an SVG data URI (no chart library needed for six of these)."""
    if len(values) < 2:
        return ""
    step = max(1, len(values) // 40)
    points = [*values[::step], values[-1]]
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    coords = " ".join(
        f"{1 + i * (width - 2) / (len(points) - 1):.1f},{height - 2 - (v - lo) / span * (height - 4):.1f}"
        for i, v in enumerate(points)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
    )
    return "data:image/svg+xml;utf8," + quote(svg)


# ---------- performance ----------

LINE_HEIGHT, WATERFALL_HEIGHT, DIVIDEND_HEIGHT, GROWTH_HEIGHT = 320, 320, 300, 380


def _legend(colors: dict[str, str]) -> dict[str, Any]:
    return {"orientation": "h", "x": 0, "y": 1.12, "font": {"size": 11, "color": colors["fg-2"]}}


def value_vs_deposits(value: Any, deposits: Any, colors: dict[str, str]) -> go.Figure:
    fig = go.Figure(
        [
            go.Scatter(
                x=value.index,
                y=value.values,
                name="Account value",
                mode="lines",
                line={"color": colors["s1"], "width": 2},
                fill="tozeroy",
                fillcolor=_alpha(colors["s1"], 0.14),
                hovertemplate="%{y:$,.0f}",
            ),
            go.Scatter(
                x=deposits.index,
                y=deposits.values,
                name="Money you put in",
                mode="lines",
                line={"color": colors["fg-2"], "width": 2, "dash": "dot", "shape": "hv"},
                hovertemplate="%{y:$,.0f}",
            ),
        ]
    )
    fig.update_layout(
        **base_layout(
            colors,
            showlegend=True,
            legend=_legend(colors),
            hovermode="x unified",
            yaxis={"tickprefix": "$", "tickformat": ",.0s"},
            margin={"l": 8, "r": 12, "t": 28, "b": 8},
        )
    )
    fig.update_layout(height=LINE_HEIGHT, autosize=True)
    return fig


def you_vs_benchmark(you: Any, bench: Any | None, bench_name: str, colors: dict[str, str]) -> go.Figure:
    traces = [
        go.Scatter(
            x=you.index,
            y=you.values,
            name="You",
            mode="lines",
            line={"color": colors["s1"], "width": 2},
            hovertemplate="%{y:.1%}",
        )
    ]
    if bench is not None:
        traces.append(
            go.Scatter(
                x=bench.index,
                y=bench.values,
                name=bench_name,
                mode="lines",
                line={"color": colors["s2"], "width": 2},
                hovertemplate="%{y:.1%}",
            )
        )
    fig = go.Figure(traces)
    fig.update_layout(
        **base_layout(
            colors,
            showlegend=True,
            legend=_legend(colors),
            hovermode="x unified",
            yaxis={"tickformat": ".0%", "zeroline": True, "zerolinecolor": colors["line-strong"]},
            margin={"l": 8, "r": 12, "t": 28, "b": 8},
        )
    )
    fig.update_layout(height=LINE_HEIGHT, autosize=True)
    return fig


def gain_waterfall(
    steps: Sequence[tuple[str, float, str]], end_value: float, colors: dict[str, str]
) -> go.Figure:
    """steps: (label, amount, "absolute" | "relative"); a final "Value now" total bar is added."""
    labels = [s[0] for s in steps] + ["Value now"]
    values = [s[1] for s in steps] + [0.0]
    measures = [s[2] for s in steps] + ["total"]
    texts = [
        money(v, decimals=0) if m == "absolute" else money(v, decimals=0, sign=True)
        for v, m in zip(values[:-1], measures[:-1], strict=True)
    ] + [money(end_value, decimals=0)]
    fig = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=measures,
            text=texts,
            textposition="outside",
            cliponaxis=False,
            textfont={"color": colors["fg-2"], "size": 11},
            connector={"line": {"color": colors["line-strong"], "width": 1}},
            increasing={"marker": {"color": colors["up"]}},
            decreasing={"marker": {"color": colors["down"]}},
            totals={"marker": {"color": colors["s1"]}},
            hovertemplate="%{x}: %{text}<extra></extra>",
        )
    )
    fig.update_layout(
        **base_layout(
            colors,
            yaxis={"tickprefix": "$", "tickformat": ",.0s"},
            xaxis={"tickfont": {"color": colors["fg-2"]}},
            margin={"l": 8, "r": 12, "t": 24, "b": 8},
        )
    )
    fig.update_layout(height=WATERFALL_HEIGHT, autosize=True)
    return fig


def dividends_by_month(months: Sequence[Any], running: bool, colors: dict[str, str]) -> go.Figure:
    x = [f"{m.year}-{m.month:02d}-15" for m in months]
    if running:
        total, ys = 0.0, []
        for m in months:
            total += m.net
            ys.append(total)
        traces = [
            go.Scatter(
                x=x,
                y=ys,
                mode="lines",
                name="Running total",
                fill="tozeroy",
                line={"color": colors["s1"], "width": 2, "shape": "hv"},
                fillcolor=_alpha(colors["s1"], 0.14),
                hovertemplate="%{y:$,.0f}<extra></extra>",
            )
        ]
        extra: dict[str, Any] = {}
    else:
        traces = [
            go.Bar(
                x=x,
                y=[m.net for m in months],
                name="Kept (net)",
                marker={"color": colors["s1"]},
                hovertemplate="Kept %{y:$,.2f}<extra></extra>",
            ),
            go.Bar(
                x=x,
                y=[-m.tax for m in months],
                name="Tax withheld",
                marker={"color": colors["s2"]},
                hovertemplate="Tax %{y:$,.2f}<extra></extra>",
            ),
            go.Bar(
                x=x,
                y=[-m.fees for m in months],
                name="Fees",
                marker={"color": colors["s4"]},
                hovertemplate="Fees %{y:$,.2f}<extra></extra>",
            ),
        ]
        extra = {"barmode": "stack", "bargap": 0.25, "showlegend": True, "legend": _legend(colors)}
    fig = go.Figure(traces)
    fig.update_layout(
        **base_layout(
            colors,
            hovermode="x unified",
            yaxis={"tickprefix": "$"},
            xaxis={"type": "date"},
            margin={"l": 8, "r": 12, "t": 28, "b": 8},
            **extra,
        )
    )
    fig.update_layout(height=DIVIDEND_HEIGHT, autosize=True)
    return fig


def bars(
    labels: Sequence[str],
    values: Sequence[float],
    texts: Sequence[str],
    colors: dict[str, str],
    *,
    horizontal: bool = False,
    signed: bool = False,
    height: int = DIVIDEND_HEIGHT,
) -> go.Figure:
    fills = [colors["up"] if v >= 0 else colors["down"] for v in values] if signed else colors["s1"]
    bar = go.Bar(
        x=list(values) if horizontal else list(labels),
        y=list(labels) if horizontal else list(values),
        orientation="h" if horizontal else "v",
        marker={"color": fills},
        text=list(texts),
        textposition="outside",
        cliponaxis=False,
        textfont={"color": colors["fg-2"], "size": 11},
        hovertemplate=("%{y}: %{x:$,.0f}" if horizontal else "%{x}: %{y:$,.0f}") + "<extra></extra>",
    )
    category = {"type": "category", "tickfont": {"color": colors["fg-2"]}}
    money_axis: dict[str, Any] = {
        "tickprefix": "$",
        "tickformat": ",.0f",
        "zeroline": True,
        "zerolinecolor": colors["line-strong"],
    }
    if values:  # room for the outside labels on both sides, so they never sit on the axis labels
        lo, hi = min(0.0, *values), max(0.0, *values)
        pad = (hi - lo) * 0.28 or 1.0
        money_axis["range"] = [lo - (pad if lo < 0 else 0.0), hi + pad]
    fig = go.Figure(bar)
    fig.update_layout(
        **base_layout(
            colors,
            bargap=0.4,
            xaxis=money_axis if horizontal else category,
            yaxis={**category, "gridcolor": "rgba(0,0,0,0)", "autorange": "reversed"}
            if horizontal
            else money_axis,
            margin={"l": 8, "r": 64 if horizontal else 12, "t": 24, "b": 8},
        )
    )
    fig.update_layout(height=height, autosize=True)
    return fig


def growth_lines(lines: Sequence[Line], slots: dict[str, int], colors: dict[str, str]) -> go.Figure:
    """Growth of $100. The portfolio is thick, holdings solid, wishlist dashed, the benchmark dotted: lines
    are told apart by style as well as colour."""
    traces = []
    for line in lines:
        slot = slots.get(line.key)
        if line.kind == "portfolio":
            style = {"color": colors["fg"], "width": 3.5}
        elif line.kind == "benchmark":
            style = {"color": colors["muted"], "width": 2, "dash": "dot"}
        else:
            style = {"color": colors[f"s{(slot or 0) + 1}"], "width": 2}
            if line.kind == "wishlist":
                style["dash"] = "dash"
        traces.append(
            go.Scatter(
                x=line.index.index,
                y=line.index.values,
                name=line.label,
                mode="lines",
                line=style,
                hovertemplate="%{y:$,.2f}",
            )
        )
    fig = go.Figure(traces)
    fig.update_layout(
        **base_layout(
            colors,
            showlegend=len(traces) > 1,
            legend=_legend(colors),
            hovermode="x unified",
            yaxis={"tickprefix": "$", "tickformat": ",.0f"},
            margin={"l": 8, "r": 12, "t": 28, "b": 8},
        )
    )
    fig.add_hline(y=100, line={"color": colors["line-strong"], "width": 1, "dash": "dot"})
    fig.update_layout(height=GROWTH_HEIGHT, autosize=True)
    return fig


def _alpha(hex_color: str, a: float) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{a})"
