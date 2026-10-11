"""Where the gains came from: start value + money added + price gains + dividends − tax − fees ± other = end.

Price gains are whatever is left once the cash items are accounted for, so the parts always add up exactly.
Commissions sit inside price gains (they are part of what each share cost).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from folioex.analytics.returns import Window
from folioex.analytics.valuation import DailySeries
from folioex.domain.models import Transaction, TxnType


@dataclass(frozen=True, slots=True)
class Breakdown:
    start_value: float
    added: float
    price_gains: float
    dividends: float
    withholding: float
    fees: float
    interest_other: float
    end_value: float


def in_window(t: Transaction, window: Window) -> bool:
    return (
        t.date >= window.start if window.from_inception else t.date > window.start
    ) and t.date <= window.end


def breakdown(transactions: Sequence[Transaction], daily: DailySeries, window: Window) -> Breakdown:
    s, e = window.start_i, window.end_i
    first = s if window.from_inception else s + 1
    start_value = 0.0 if window.from_inception else float(daily.value.iloc[s])
    added = float(daily.flows.iloc[first : e + 1].sum())
    sums = {k: 0.0 for k in ("div", "tax", "fees", "other")}
    for t in transactions:
        if not in_window(t, window):
            continue
        if t.type is TxnType.DIVIDEND:
            sums["div"] += t.net
        elif t.type is TxnType.WITHHOLDING:
            sums["tax"] += t.net
        elif t.type is TxnType.FEE:
            sums["fees"] += t.net
        elif t.type in (TxnType.INTEREST, TxnType.OTHER):
            sums["other"] += t.net
    end_value = float(daily.value.iloc[e])
    income = sum(sums.values())
    return Breakdown(
        start_value=start_value,
        added=added,
        price_gains=end_value - start_value - added - income,
        dividends=sums["div"],
        withholding=sums["tax"],
        fees=sums["fees"],
        interest_other=sums["other"],
        end_value=end_value,
    )
