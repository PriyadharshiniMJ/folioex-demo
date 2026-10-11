"""The candlestick progress bar (criterion P4) and its caption."""

import pytest

from folioex.jobs.runner import Snapshot
from folioex.ui.components.candle_progress import candle_figure, make_candles, progress_view
from folioex.ui.theme import chart_colors


def test_twenty_five_well_formed_candles_on_a_rising_trend() -> None:
    candles = make_candles(seed=7, green_ratio=0.65)
    assert len(candles) == 25
    for c in candles:
        assert c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
        assert (c.close > c.open) == c.green
    mid = [(c.open + c.close) / 2 for c in candles]
    assert sum(mid[-5:]) > sum(mid[:5])


def test_final_candle_closes_green_at_the_high() -> None:
    for seed in range(20):
        last = make_candles(seed=seed, green_ratio=0.0)[-1]
        assert last.green and last.close == last.high


def test_colours_are_deterministic_per_seed() -> None:
    assert make_candles(seed=3, green_ratio=0.65) == make_candles(seed=3, green_ratio=0.65)
    assert make_candles(seed=3, green_ratio=0.65) != make_candles(seed=4, green_ratio=0.65)


def test_green_ratio_is_respected_on_average() -> None:
    greens = [c.green for seed in range(200) for c in make_candles(seed=seed, green_ratio=0.65)[:-1]]
    assert 0.58 < sum(greens) / len(greens) < 0.72


def trace_points(fig: object) -> dict[str, int]:
    return {t.name: len(t.x) for t in fig.data}  # type: ignore[attr-defined]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_figure_states_while_running(theme: str) -> None:
    colors = chart_colors(theme)
    fig = candle_figure(make_candles(1, 0.65), stage=11, pulse=False, colors=colors)
    points = trace_points(fig)
    assert points["done-up"] + points["done-down"] == 11
    assert points["current"] == 1
    assert points["pending"] == 13
    assert sum(points.values()) == 25
    pending = next(t for t in fig.data if t.name == "pending")
    assert pending.increasing.fillcolor == pending.decreasing.fillcolor == colors["c-pend"]
    done_colors = {t.increasing.fillcolor for t in fig.data if t.name.startswith("done")}
    assert done_colors == {colors["c-up"], colors["c-down"]}
    assert fig.layout.xaxis.rangeslider.visible is False


def test_figure_when_finished_has_no_pending_or_current() -> None:
    fig = candle_figure(make_candles(1, 0.65), stage=25, pulse=False, colors=chart_colors("light"))
    points = trace_points(fig)
    assert points["current"] == points["pending"] == 0
    assert points["done-up"] + points["done-down"] == 25


def test_progress_view_text_and_accessible_label() -> None:
    snap = Snapshot("job", "running", 10, "Fetch prices", 9, 16, "stocks", None)
    view = progress_view(snap, theme="light", pulse=True, green_ratio=0.65)
    assert view.caption == f"Stage 11/25 · Fetch prices (9/16 stocks) · {snap.percent}%"
    assert view.percent_text == f"{snap.percent}%"
    assert view.aria_label == f"Processing {snap.percent}% complete"
    assert len(view.figure.data) == 4
