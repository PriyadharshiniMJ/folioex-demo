"""What FolioEx needs from a market data source, independent of where it comes from."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataError(Exception):
    """The source couldn't be reached or returned nothing usable (network, outage, rate limit)."""


@dataclass(frozen=True, slots=True)
class Profile:
    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    is_fund: bool = False
    pe: float | None = None
    dividend_yield: float | None = None  # a fraction: 0.015 = 1.5%


@dataclass(slots=True)
class PriceData:
    """Daily closes per symbol.

    ``close`` is split-adjusted to today's share count (as Yahoo Finance reports it);
    ``adj_close`` also folds in
    dividends (total return). Dividends are per share, split-adjusted; splits are (date, ratio).
    """

    close: pd.DataFrame
    adj_close: pd.DataFrame
    dividends: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    splits: dict[str, list[tuple[date, float]]] = field(default_factory=dict)

    @classmethod
    def from_closes(
        cls,
        closes: Mapping[str, Mapping[date, float]],
        *,
        adj: Mapping[str, Mapping[date, float]] | None = None,
        dividends: Mapping[str, Sequence[tuple[date, float]]] | None = None,
        splits: Mapping[str, Sequence[tuple[date, float]]] | None = None,
    ) -> PriceData:
        close = _frame(closes)
        adj_frame = _frame(adj) if adj else close.copy()
        return cls(
            close=close,
            adj_close=adj_frame,
            dividends={k: list(v) for k, v in (dividends or {}).items() if k in close.columns},
            splits={k: list(v) for k, v in (splits or {}).items()},
        )

    @classmethod
    def empty(cls) -> PriceData:
        return cls.from_closes({})

    def symbols(self) -> list[str]:
        return [str(c) for c in self.close.columns if self.close[c].notna().any()]

    def series(self, symbol: str) -> pd.Series:
        if symbol not in self.close.columns:
            return pd.Series(dtype=float)
        return self.close[symbol].dropna()

    def last(self, symbol: str) -> tuple[date, float] | None:
        s = self.series(symbol)
        if s.empty:
            return None
        return s.index[-1].date(), float(s.iloc[-1])

    def previous_close(self, symbol: str) -> float | None:
        s = self.series(symbol)
        return float(s.iloc[-2]) if len(s) >= 2 else None

    def price_on(self, symbol: str, day: date) -> float | None:
        """The close on ``day`` or the last one before it."""
        s = self.series(symbol)
        s = s[s.index <= pd.Timestamp(day)]
        return None if s.empty else float(s.iloc[-1])

    def last_date(self) -> date | None:
        dates = [self.series(s).index[-1].date() for s in self.symbols()]
        return max(dates) if dates else None

    @staticmethod
    def combine(parts: Iterable[PriceData]) -> PriceData:
        parts = [p for p in parts if not p.close.empty]
        if not parts:
            return PriceData.empty()
        return PriceData(
            close=pd.concat([p.close for p in parts], axis=1).sort_index(),
            adj_close=pd.concat([p.adj_close for p in parts], axis=1).sort_index(),
            dividends={k: v for p in parts for k, v in p.dividends.items()},
            splits={k: v for p in parts for k, v in p.splits.items()},
        )


def _frame(values: Mapping[str, Mapping[date, float]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame(index=pd.DatetimeIndex([]), dtype=float)
    series = {
        sym: pd.Series({pd.Timestamp(d): float(v) for d, v in points.items()})
        for sym, points in values.items()
    }
    return pd.DataFrame(series).sort_index()


class MarketDataProvider(Protocol):
    def prices(self, symbols: Sequence[str], start: date, end: date) -> PriceData:
        """Daily data for the symbols it knows; unknown symbols are simply absent. Raises MarketDataError."""

    def profiles(self, symbols: Sequence[str]) -> dict[str, Profile]:
        """Best-effort company details; missing ones are absent. May raise MarketDataError."""
