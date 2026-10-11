"""The 25-candle progress bar: one candle per processing stage.

Pending stages are gray; finished ones are green or red purely for fun (seeded per job, on a gently
rising trend, and the last candle always closes green at its high). Errors are never shown by colour.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import plotly.graph_objects as go

from folioex.jobs.runner import TOTAL, Snapshot
from folioex.jobs.stages import STAGES
from folioex.ui.theme import chart_colors


@dataclass(frozen=True, slots=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    green: bool


def make_candles(seed: int, green_ratio: float) -> list[Candle]:
    rng = random.Random(seed)
    candles: list[Candle] = []
    for i in range(TOTAL):
        last = i == TOTAL - 1
        mid = 100 + i * 3 + rng.gauss(0, 2.2)
        green = last or rng.random() < green_ratio
        body = 1.6 + rng.random() * 3
        low_end, high_end = mid - body / 2, mid + body / 2
        open_, close = (low_end, high_end) if green else (high_end, low_end)
        high = close if last else max(open_, close) + 0.6 + rng.random() * 2
        low = min(open_, close) - 0.6 - rng.random() * 2
        candles.append(Candle(open_, high, low, close, green))
    return candles


def _trace(name: str, points: list[tuple[int, Candle]], color: str, opacity: float = 1.0) -> go.Candlestick:
    style = {"line": {"color": color, "width": 1.5}, "fillcolor": color}
    return go.Candlestick(
        name=name,
        x=[i + 1 for i, _ in points],
        open=[c.open for _, c in points],
        high=[c.high for _, c in points],
        low=[c.low for _, c in points],
        close=[c.close for _, c in points],
        text=[f"{i + 1}. {STAGES[i].label}" for i, _ in points],
        hoverinfo="text",
        increasing=style,
        decreasing=style,
        whiskerwidth=0.4,
        opacity=opacity,
        showlegend=False,
    )


def candle_figure(candles: list[Candle], *, stage: int, pulse: bool, colors: dict[str, str]) -> go.Figure:
    indexed = list(enumerate(candles))
    done = [(i, c) for i, c in indexed if i < stage]
    current = [(i, c) for i, c in indexed if i == stage]
    pending = [(i, c) for i, c in indexed if i > stage]
    fig = go.Figure(
        [
            _trace("done-up", [(i, c) for i, c in done if c.green], colors["c-up"]),
            _trace("done-down", [(i, c) for i, c in done if not c.green], colors["c-down"]),
            _trace("current", current, colors["c-now"], 0.45 if pulse else 1.0),
            _trace("pending", pending, colors["c-pend"]),
        ]
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 4, "r": 4, "t": 4, "b": 4},
        height=190,
        hoverlabel={"bgcolor": colors["surface"], "font": {"color": colors["fg"]}},
        xaxis={
            "visible": False,
            "range": [0.3, TOTAL + 0.7],
            "fixedrange": True,
            "rangeslider": {"visible": False},
        },
        yaxis={"visible": False, "fixedrange": True},
        transition={"duration": 0},
    )
    return fig


@dataclass(frozen=True, slots=True)
class ProgressView:
    figure: go.Figure
    caption: str
    percent_text: str
    aria_label: str


def progress_view(snapshot: Snapshot, *, theme: str | None, pulse: bool, green_ratio: float) -> ProgressView:
    seed = int(snapshot.job_id, 16) if all(ch in "0123456789abcdef" for ch in snapshot.job_id) else 0
    candles = make_candles(seed, green_ratio)
    figure = candle_figure(candles, stage=snapshot.stage, pulse=pulse, colors=chart_colors(theme))
    return ProgressView(
        figure=figure,
        caption=snapshot.caption,
        percent_text=f"{snapshot.percent}%",
        aria_label=f"Processing {snapshot.percent}% complete",
    )
