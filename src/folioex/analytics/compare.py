"""Compare tab maths: stocks rebased to 100, risk figures, best-in-column stars, colours and the pick limit.

Prices are total-return (dividends reinvested), so a dividend payer isn't penalised against a growth stock.
The whole-portfolio line is the time-weighted index: money you add is never counted as growth.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from folioex.analytics.returns import annualize, twr_series

Kind = Literal["portfolio", "holding", "wishlist", "benchmark"]
TRADING_DAYS = 252
_MIN_RETURNS = 20  # fewer daily returns than this and volatility would be guesswork
_PICKS: tuple[Kind, ...] = ("holding", "wishlist")


@dataclass(frozen=True, slots=True)
class Stats:
    ret: float
    annual: float | None  # only for periods of a year or more
    volatility: float | None  # yearly size of the ups and downs
    drawdown: float  # biggest fall from a high, ≤ 0
    sharpe: float | None


@dataclass(frozen=True, slots=True)
class Line:
    key: str
    label: str
    kind: Kind
    index: pd.Series  # starts at 100
    stats: Stats
    since: date | None  # set when the line starts after the period does (a newer listing)
    dividend_yield: float | None = None
    pe: float | None = None
    market_cap: float | None = None


def rebase(prices: pd.Series, dates: pd.DatetimeIndex) -> pd.Series | None:
    """Prices on ``dates`` scaled to start at 100. Gaps carry the last price forward; a stock that starts
    trading later starts later. None when fewer than two prices exist."""
    clean = prices.dropna().sort_index()
    if clean.empty:
        return None
    onto = clean.reindex(dates, method="ffill").dropna()
    if len(onto) < 2:
        return None
    return onto / float(onto.iloc[0]) * 100.0


def stats_of(index: pd.Series, rf: float) -> Stats:
    first, last = float(index.iloc[0]), float(index.iloc[-1])
    ret = last / first - 1
    days = (pd.Timestamp(index.index[-1]) - pd.Timestamp(index.index[0])).days
    returns = index.pct_change().dropna()
    vol: float | None = None
    sharpe: float | None = None
    if len(returns) >= _MIN_RETURNS:
        vol = float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS))
        if vol > 0:
            sharpe = (float(returns.mean()) * TRADING_DAYS - rf) / vol
    drawdown = float((index / index.cummax() - 1).min())
    return Stats(ret, annualize(ret, days), vol, drawdown, sharpe)


def portfolio_index(value: pd.Series, flows: pd.Series, s: int, e: int) -> pd.Series:
    """The account's time-weighted growth, starting at 100."""
    return (1 + twr_series(value, flows, s, e)) * 100.0


def make_line(  # noqa: PLR0917 - a chart line has this many independent facts
    key: str,
    label: str,
    kind: Kind,
    prices: pd.Series,
    dates: pd.DatetimeIndex,
    rf: float,
    *,
    dividend_yield: float | None = None,
    pe: float | None = None,
    market_cap: float | None = None,
) -> Line | None:
    index = rebase(prices, dates)
    if index is None:
        return None
    started = pd.Timestamp(index.index[0]).date()
    since = started if started > pd.Timestamp(dates[0]).date() else None
    return Line(key, label, kind, index, stats_of(index, rf), since, dividend_yield, pe, market_cap)


# higher is better, except volatility; the biggest drop is best when it is smallest (closest to zero)
_BEST: dict[str, Literal["max", "min"]] = {
    "ret": "max",
    "annual": "max",
    "volatility": "min",
    "drawdown": "max",
    "sharpe": "max",
    "dividend_yield": "max",
}


def _value(line: Line, column: str) -> float | None:
    source = line if column == "dividend_yield" else line.stats
    value = getattr(source, column)
    return None if value is None else float(value)


def best_keys(lines: Sequence[Line]) -> dict[str, set[str]]:
    """For each column, the picks sharing the best value. The portfolio and benchmark never win a star,
    and a column needs at least two values to have a best."""
    picks = [line for line in lines if line.kind in _PICKS]
    out: dict[str, set[str]] = {}
    for column, how in _BEST.items():
        values = [(line.key, v) for line in picks if (v := _value(line, column)) is not None]
        if len(values) < 2:
            out[column] = set()
            continue
        target = (max if how == "max" else min)(v for _, v in values)
        out[column] = {key for key, v in values if math.isclose(v, target, rel_tol=1e-9, abs_tol=1e-12)}
    return out


def beat_count(lines: Sequence[Line]) -> tuple[int, int] | None:
    """(picks that out-grew the whole portfolio, picks) or None without a portfolio line or picks."""
    portfolio = next((line for line in lines if line.kind == "portfolio"), None)
    picks = [line for line in lines if line.kind in _PICKS]
    if portfolio is None or not picks:
        return None
    return sum(line.stats.ret > portfolio.stats.ret for line in picks), len(picks)


def assign_slots(current: Mapping[str, int], selected: Sequence[str], size: int) -> dict[str, int]:
    """A colour slot per picked ticker. A ticker keeps its slot while picked; freed slots are reused."""
    kept = {k: v for k, v in current.items() if k in selected and 0 <= v < size}
    free = [i for i in range(size) if i not in kept.values()]
    for key in selected:
        if key not in kept and free:
            kept[key] = free.pop(0)
    return {k: kept[k] for k in selected if k in kept}


def clamp_picks(
    holdings: Sequence[str], wishlist: Sequence[str], limit: int, *, newest: Literal["holdings", "wishlist"]
) -> tuple[list[str], list[str], bool]:
    """Keep at most ``limit`` picks in total, dropping from the end of the list that was just changed.
    A ticker picked in both lists counts once, as a holding. The last value says if anything was dropped."""
    held = list(dict.fromkeys(holdings))
    wish = [s for s in dict.fromkeys(wishlist) if s not in held]
    trimmed = False
    target = held if newest == "holdings" else wish
    while len(held) + len(wish) > limit and target:
        target.pop()
        trimmed = True
    other = wish if newest == "holdings" else held
    while len(held) + len(wish) > limit and other:
        other.pop()
        trimmed = True
    return held, wish, trimmed
