"""The account, day by day: value, cash, money put in, and shares held.

Shares are kept in *today's* share basis so they multiply straight into Yahoo's split-adjusted closes:
a pre-split purchase of q shares counts as q × (product of later splits). A split row in the file is then
a no-op when market data knows that split; otherwise it adds shares as written.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from folioex.domain.models import EXTERNAL_FLOW_TYPES, POSITION_TYPES, Transaction, TxnType
from folioex.market_data.base import PriceData

_SPLIT_MATCH = timedelta(days=7)


@dataclass(slots=True)
class DailySeries:
    value: pd.Series  # account value at each day's close
    cash: pd.Series
    invested: pd.Series
    flows: pd.Series  # money in (+) or out (−) of the account that day
    net_deposits: pd.Series  # running total of flows
    shares: pd.DataFrame  # today's share basis
    prices: pd.DataFrame  # closes, forward-filled, today's share basis

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.value.index)

    def day(self, i: int) -> date:
        return self.index[i].date()

    @classmethod
    def empty(cls) -> DailySeries:
        idx = pd.DatetimeIndex([])
        blank = pd.Series(dtype=float, index=idx)
        return cls(blank, blank, blank, blank, blank, pd.DataFrame(index=idx), pd.DataFrame(index=idx))


def factor_after(splits: Sequence[tuple[date, float]], day: date) -> float:
    factor = 1.0
    for split_day, ratio in splits:
        if split_day > day and ratio > 0:
            factor *= ratio
    return factor


def build_daily(transactions: Sequence[Transaction], prices: PriceData) -> DailySeries:
    if not transactions:
        return DailySeries.empty()
    first = min(t.date for t in transactions)
    market_end = prices.last_date()
    last_txn = max(t.date for t in transactions)
    last = max(last_txn, market_end) if market_end else last_txn
    stamps = {ts for ts in prices.close.index if first <= ts.date() <= last}
    stamps |= {pd.Timestamp(t.date) for t in transactions}
    idx = pd.DatetimeIndex(sorted(stamps))
    position = {ts: i for i, ts in enumerate(idx)}
    symbols = sorted({t.symbol for t in transactions if t.symbol and t.type in POSITION_TYPES})
    column = {s: j for j, s in enumerate(symbols)}

    cash_delta = np.zeros(len(idx))
    flow_values = np.zeros(len(idx))
    deltas = np.zeros((len(idx), len(symbols)))
    transfers: list[tuple[int, str, float]] = []
    trade_prices: dict[str, dict[pd.Timestamp, float]] = {s: {} for s in symbols}
    for t in transactions:
        i = position[pd.Timestamp(t.date)]
        cash_delta[i] += t.net
        if t.type in EXTERNAL_FLOW_TYPES:
            flow_values[i] += t.net
        symbol = t.symbol
        if symbol is None or symbol not in column or t.type not in POSITION_TYPES:
            continue
        splits = prices.splits.get(symbol, [])
        factor = factor_after(splits, t.date)
        if t.type is TxnType.SPLIT and any(abs((d - t.date).days) <= _SPLIT_MATCH.days for d, _ in splits):
            continue  # already reflected in today's share basis
        deltas[i, column[symbol]] += t.quantity * factor
        if t.type in (TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT):
            transfers.append((i, symbol, t.quantity * factor))
        if t.price:
            trade_prices[symbol][pd.Timestamp(t.date)] = t.price / factor

    shares = pd.DataFrame({s: _running_nonnegative(deltas[:, column[s]]) for s in symbols}, index=idx)
    price_frame = pd.DataFrame(index=idx, columns=symbols, dtype=float)
    for s in symbols:
        market = prices.close[s].reindex(idx) if s in prices.close.columns else pd.Series(np.nan, index=idx)
        if market.notna().any():
            price_frame[s] = market.ffill().bfill()
        else:
            fallback = pd.Series(trade_prices[s], dtype=float).reindex(idx)
            price_frame[s] = fallback.ffill().bfill().fillna(0.0)
    for i, s, qty in transfers:  # shares moved in or out are money crossing the account boundary
        flow_values[i] += qty * float(price_frame[s].to_numpy()[i])

    flows = pd.Series(flow_values, index=idx)
    invested = (shares * price_frame).sum(axis=1) if symbols else pd.Series(0.0, index=idx)
    cash = pd.Series(cash_delta, index=idx).cumsum()
    return DailySeries(
        value=invested + cash,
        cash=cash,
        invested=invested,
        flows=flows,
        net_deposits=flows.cumsum(),
        shares=shares,
        prices=price_frame,
    )


def _running_nonnegative(deltas: np.ndarray) -> np.ndarray:
    """Cumulative shares, never below zero (selling shares we never saw bought is a history gap)."""
    out = np.empty_like(deltas, dtype=float)
    held = 0.0
    for i, change in enumerate(deltas):
        held = max(0.0, held + float(change))
        out[i] = held
    return out
