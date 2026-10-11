"""Yahoo Finance via yfinance. Unofficial and sometimes flaky, so everything is wrapped and batched."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

from folioex.market_data.base import MarketDataError, PriceData, Profile

logger = logging.getLogger("folioex.market")

_FUND_TYPES = {"ETF", "MUTUALFUND"}
_TIMEOUT = 20


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


class YFinanceProvider:
    def prices(self, symbols: Sequence[str], start: date, end: date) -> PriceData:
        try:
            frame = yf.download(
                list(symbols),
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=_TIMEOUT,
                multi_level_index=True,
            )
        except Exception as exc:  # yfinance raises a variety of network errors
            raise MarketDataError(str(exc)) from exc
        if frame is None or frame.empty:
            raise MarketDataError("no data returned")
        return _parse(frame, symbols)

    def profiles(self, symbols: Sequence[str]) -> dict[str, Profile]:
        def one(symbol: str) -> Profile | None:
            try:
                info = yf.Ticker(symbol).info or {}
            except Exception:
                logger.info("No company details for %s", symbol)
                return None
            if not info:
                return None
            fund = str(info.get("quoteType", "")).upper() in _FUND_TYPES
            yield_pct = _num(info.get("dividendYield"))
            return Profile(
                symbol=symbol,
                name=info.get("longName") or info.get("shortName"),
                sector=None if fund else info.get("sector"),
                industry=None if fund else info.get("industry"),
                market_cap=_num(info.get("marketCap")),
                is_fund=fund,
                pe=_num(info.get("trailingPE")),
                dividend_yield=None
                if yield_pct is None
                else yield_pct / 100,  # yfinance reports a percentage
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            found = dict(zip(symbols, pool.map(one, symbols), strict=True))
        return {s: p for s, p in found.items() if p is not None}


def _parse(frame: pd.DataFrame, symbols: Sequence[str]) -> PriceData:
    closes: dict[str, dict[date, float]] = {}
    adj: dict[str, dict[date, float]] = {}
    dividends: dict[str, list[tuple[date, float]]] = {}
    splits: dict[str, list[tuple[date, float]]] = {}
    present = set(frame.columns.get_level_values(0)) if isinstance(frame.columns, pd.MultiIndex) else set()
    for s in symbols:
        if s not in present:
            continue
        data = frame[s]
        if "Close" not in data or data["Close"].dropna().empty:
            continue
        days = [pd.Timestamp(ts).date() for ts in data.index]
        closes[s] = {d: v for d, v in zip(days, data["Close"], strict=True) if _num(v) is not None}
        adj_col = data["Adj Close"] if "Adj Close" in data else data["Close"]
        adj[s] = {d: v for d, v in zip(days, adj_col, strict=True) if _num(v) is not None}
        if "Dividends" in data:
            dividends[s] = [
                (d, float(v)) for d, v in zip(days, data["Dividends"], strict=True) if (_num(v) or 0) > 0
            ]
        if "Stock Splits" in data:
            splits[s] = [
                (d, float(v)) for d, v in zip(days, data["Stock Splits"], strict=True) if (_num(v) or 0) > 0
            ]
    if not closes:
        raise MarketDataError("none of the requested symbols returned prices")
    return PriceData.from_closes(closes, adj=adj, dividends=dividends, splits=splits)
