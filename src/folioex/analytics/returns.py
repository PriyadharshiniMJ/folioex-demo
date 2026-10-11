"""Periods and returns.

Time-weighted return (TWR): how well the investments did, ignoring when money was added. Flows count at the
end of their day: r_t = (V_t − F_t) / V_{t−1} − 1.
Money-weighted return (XIRR): what the money actually earned per year given when it went in; Actual/365.
Returns are annualized only for periods of a year or more.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

PERIODS = ("1M", "3M", "6M", "YTD", "1Y", "3Y", "All")
_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36}
_YEAR = 365


@dataclass(frozen=True, slots=True)
class Window:
    start_i: int
    end_i: int
    start: date
    end: date
    clamped: bool  # asked for more history than exists
    from_inception: bool

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True, slots=True)
class MonthReturn:
    year: int
    month: int
    ret: float


def _months_back(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _last_index(days: Sequence[date], test: object) -> int | None:
    found = None
    for i, d in enumerate(days):
        if test(d):  # type: ignore[operator]
            found = i
    return found


def resolve_period(code: str, dates: pd.DatetimeIndex, custom: tuple[date, date] | None = None) -> Window:
    """Map "1Y", "YTD", "All", "Custom" … onto day indexes. A window starts at the close before it begins."""
    days = [ts.date() for ts in dates]
    if not days:
        today = date.today()
        return Window(0, 0, today, today, False, True)
    end_i = len(days) - 1
    clamped = False
    if code == "Custom" and custom is not None:
        lo, hi = custom
        end_i = _last_index(days, lambda d: d <= hi) or 0
        start = _last_index(days, lambda d: d < lo)
    elif code in _MONTHS or code == "YTD":
        target = (
            date(days[end_i].year - 1, 12, 31) if code == "YTD" else _months_back(days[end_i], _MONTHS[code])
        )
        start = _last_index(days, lambda d: d <= target)
    else:
        start = 0
    if start is None:
        start, clamped = 0, code != "All"
    start_i = min(start, max(0, end_i - 1))
    return Window(start_i, end_i, days[start_i], days[end_i], clamped, start_i == 0)


def twr_series(value: pd.Series, flows: pd.Series, s: int, e: int) -> pd.Series:
    v, f = value.to_numpy(), flows.to_numpy()
    growth, out = 1.0, [0.0]
    for i in range(s + 1, e + 1):
        if v[i - 1] > 0:
            growth *= (v[i] - f[i]) / v[i - 1]
        out.append(growth - 1)
    return pd.Series(out, index=value.index[s : e + 1])


def twr(value: pd.Series, flows: pd.Series, s: int, e: int) -> float:
    series = twr_series(value, flows, s, e)
    return float(series.iloc[-1]) if len(series) else 0.0


def xirr(cashflows: Sequence[tuple[date, float]]) -> float | None:
    """Annual rate r with Σ cf / (1 + r)^(days/365) = 0, or None when no such rate exists."""
    if len(cashflows) < 2:
        return None
    t0 = min(d for d, _ in cashflows)

    def npv(rate: float) -> float:
        return float(sum(cf / (1 + rate) ** ((d - t0).days / _YEAR) for d, cf in cashflows))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9 or hi - lo < 1e-12:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def money_weighted(value: pd.Series, flows: pd.Series, window: Window) -> float | None:
    if window.days < _YEAR:
        return None
    s, e = window.start_i, window.end_i
    cashflows: list[tuple[date, float]] = [(window.start, -float(value.iloc[s]))]
    for i in range(s + 1, e + 1):
        if flows.iloc[i]:
            cashflows.append((value.index[i].date(), -float(flows.iloc[i])))
    cashflows.append((window.end, float(value.iloc[e])))
    return xirr(cashflows)


def annualize(ret: float, days: int) -> float | None:
    if days < _YEAR or ret <= -1:
        return None
    return float((1 + ret) ** (_YEAR / days) - 1)


def monthly_returns(value: pd.Series, flows: pd.Series, window: Window) -> list[MonthReturn]:
    v, f = value.to_numpy(), flows.to_numpy()
    growth: dict[tuple[int, int], float] = {}
    for i in range(window.start_i + 1, window.end_i + 1):
        if v[i - 1] <= 0:
            continue
        day = value.index[i]
        key = (day.year, day.month)
        growth[key] = growth.get(key, 1.0) * (v[i] - f[i]) / v[i - 1]
    return [MonthReturn(y, m, g - 1) for (y, m), g in sorted(growth.items())]
