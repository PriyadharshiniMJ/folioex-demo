"""Everything the Performance tab shows for one period and one benchmark, computed from the daily series.

Per-stock period return uses Modified Dietz: gain ÷ average money invested in the stock during the period,
where money added part-way counts for the share of the period it was invested. Dividends count as gain.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from folioex.analytics.attribution import Breakdown, breakdown, in_window
from folioex.analytics.benchmark import Shadow, index_return, shadow
from folioex.analytics.income import Income, YearIncome, dividend_income
from folioex.analytics.performers import Performer, split_best_worst
from folioex.analytics.returns import (
    MonthReturn,
    Window,
    money_weighted,
    monthly_returns,
    resolve_period,
    twr_series,
)
from folioex.analytics.valuation import DailySeries
from folioex.config import Settings
from folioex.domain.models import Transaction, TxnType

if TYPE_CHECKING:
    from folioex.dataset import Dataset

_EPS = 1e-9
_TRADES = (TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT)
_INCOME = (TxnType.DIVIDEND, TxnType.WITHHOLDING, TxnType.FEE)


@dataclass(slots=True)
class PerformanceView:
    window: Window
    twr: float
    bench_code: str
    bench_twr: float | None
    excess: float | None
    xirr: float | None
    added: float
    realized: float
    breakdown: Breakdown
    value: pd.Series
    deposits: pd.Series
    you: pd.Series
    bench: pd.Series | None
    shadow: Shadow | None
    months: list[MonthReturn]
    income: Income
    all_years: list[YearIncome]  # whole history, for fair year-on-year comparisons
    last12_net: float
    dividend_growth: tuple[int, float] | None  # (year, growth vs the year before)
    best: list[Performer]
    worst: list[Performer]
    contribution: list[tuple[str, float]]
    realized_by_year: dict[int, float] = field(default_factory=dict)


@dataclass(slots=True)
class _PositionResult:
    symbol: str
    gain: float
    capital: float
    first: date
    last: date
    sold: bool


def _position_results(
    transactions: Sequence[Transaction], daily: DailySeries, window: Window
) -> list[_PositionResult]:
    s, e = window.start_i, window.end_i
    span = max(1, (window.end - window.start).days)
    by_symbol: defaultdict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if (
            t.symbol
            and t.type in (*_TRADES, *_INCOME)
            and in_window(t, window)
            and not (window.from_inception and t.date == window.start and t.type in _TRADES)
        ):
            by_symbol[t.symbol].append(t)
    position = {ts: i for i, ts in enumerate(daily.index)}
    results = []
    for symbol in daily.shares.columns:
        held = daily.shares[symbol].to_numpy()
        price = daily.prices[symbol].to_numpy()
        mv0, mv1 = held[s] * price[s], held[e] * price[e]
        flows: list[tuple[date, float]] = []
        income = 0.0
        for t in by_symbol.get(symbol, []):
            if t.type in (TxnType.BUY, TxnType.SELL):
                flows.append((t.date, -t.net))
            elif t.type in (TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT):
                i = position[pd.Timestamp(t.date)]
                flows.append((t.date, float((held[i] - (held[i - 1] if i else 0.0)) * price[i])))
            else:
                income += t.net
        if mv0 <= _EPS and not flows:
            continue
        # A day counts as owned if shares were held at its close or going into it (the sale day itself).
        owned = [daily.day(i) for i in range(s, e + 1) if held[i] > _EPS or (i > s and held[i - 1] > _EPS)]
        first = window.start if held[s] > _EPS else min(d for d, _ in flows)
        last = owned[-1] if owned else first
        capital = mv0 + sum(amount * (window.end - d).days / span for d, amount in flows)
        gain = mv1 - mv0 - sum(amount for _, amount in flows) + income
        results.append(_PositionResult(symbol, gain, capital, first, last, held[e] <= _EPS))
    return results


def period_performers(
    transactions: Sequence[Transaction],
    daily: DailySeries,
    window: Window,
    *,
    min_days: int,
    min_capital: float,
    count: int,
    names: Mapping[str, str],
) -> tuple[list[Performer], list[Performer]]:
    eligible = [
        Performer(
            r.symbol, names.get(r.symbol, r.symbol), r.gain / r.capital, r.gain, r.first, r.last, r.sold
        )
        for r in _position_results(transactions, daily, window)
        if r.capital >= max(min_capital, _EPS) and (r.last - r.first).days >= min_days
    ]
    return split_best_worst(eligible, key=lambda p: p.ret, count=count)


def _bench_series(data: Dataset, code: str) -> pd.Series | None:
    adj = data.market.prices.adj_close
    if code not in adj.columns or adj[code].dropna().empty:
        return None
    return adj[code].reindex(data.daily.index).ffill().bfill()


def performance_view(
    data: Dataset,
    settings: Settings,
    *,
    period: str,
    benchmark: str,
    custom: tuple[date, date] | None = None,
) -> PerformanceView:
    daily, txns = data.daily, data.ingest.transactions
    window = resolve_period(period, daily.index, custom)
    s, e = window.start_i, window.end_i
    you = twr_series(daily.value, daily.flows, s, e)
    bench = _bench_series(data, benchmark)
    bench_twr = index_return(bench, s, e) if bench is not None else None
    bench_curve = (bench.iloc[s : e + 1] / float(bench.iloc[s]) - 1) if bench is not None else None
    first = s if window.from_inception else s + 1
    names = {r.symbol: r.name for r in data.holdings.rows} | {
        sym: p.name for sym, p in data.market.profiles.items() if p.name
    }
    best, worst = period_performers(
        txns,
        daily,
        window,
        min_days=settings.performers.min_days,
        min_capital=settings.performers.min_avg_capital,
        count=settings.performers.count,
        names=names,
    )
    contribution = sorted(
        ((r.symbol, r.gain) for r in _position_results(txns, daily, window)), key=lambda x: -abs(x[1])
    )[:8]
    realized_by_year: defaultdict[int, float] = defaultdict(float)
    for g in data.book.realized:
        if (
            g.date >= window.start if window.from_inception else g.date > window.start
        ) and g.date <= window.end:
            realized_by_year[g.date.year] += g.gain
    as_of = window.end
    everything = dividend_income(txns, date.min, as_of, include_start=True)
    full_years = [y for y in everything.by_year if y.year < as_of.year]
    growth = (
        (full_years[-1].year, full_years[-1].yoy) if full_years and full_years[-1].yoy is not None else None
    )
    return PerformanceView(
        window=window,
        twr=float(you.iloc[-1]) if len(you) else 0.0,
        bench_code=benchmark,
        bench_twr=bench_twr,
        excess=(float(you.iloc[-1]) - bench_twr) if bench_twr is not None and len(you) else None,
        xirr=money_weighted(daily.value, daily.flows, window),
        added=float(daily.flows.iloc[first : e + 1].sum()),
        realized=sum(realized_by_year.values()),
        breakdown=breakdown(txns, daily, window),
        value=daily.value.iloc[s : e + 1],
        deposits=daily.net_deposits.iloc[s : e + 1],
        you=you,
        bench=bench_curve,
        shadow=shadow(bench, daily.value, daily.flows, s, e) if bench is not None else None,
        months=monthly_returns(daily.value, daily.flows, window),
        income=dividend_income(txns, window.start, window.end, include_start=window.from_inception),
        all_years=everything.by_year,
        last12_net=dividend_income(txns, as_of - timedelta(days=365), as_of, include_start=False).net,
        dividend_growth=growth,
        best=best,
        worst=worst,
        contribution=contribution,
        realized_by_year=dict(sorted(realized_by_year.items())),
    )
