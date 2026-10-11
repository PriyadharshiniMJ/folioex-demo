"""The benchmark "shadow portfolio": what if every dollar had gone into the index on the same days?

It starts with the account's value at the window start, buys index units with each deposit and sells
with each withdrawal. Prices are total-return (dividends reinvested), like the account's own return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass(slots=True)
class Shadow:
    values: pd.Series
    capped: list[date] = field(default_factory=list)  # withdrawals larger than the shadow could cover


def shadow(bench: pd.Series, value: pd.Series, flows: pd.Series, s: int, e: int) -> Shadow:
    b, f = bench.to_numpy(), flows.to_numpy()
    units = float(value.iloc[s]) / b[s] if b[s] > 0 else 0.0
    out = [units * b[s]]
    capped: list[date] = []
    for i in range(s + 1, e + 1):
        if b[i] > 0:
            units += f[i] / b[i]
        if units < 0:
            units = 0.0
            capped.append(bench.index[i].date())
        out.append(units * b[i])
    return Shadow(pd.Series(out, index=bench.index[s : e + 1]), capped)


def index_return(bench: pd.Series, s: int, e: int) -> float | None:
    start, end = float(bench.iloc[s]), float(bench.iloc[e])
    return end / start - 1 if start > 0 else None
