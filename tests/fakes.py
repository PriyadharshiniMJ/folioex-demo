"""A deterministic stand-in for Yahoo Finance: same ticker → same prices, any ticker works, no network."""

from __future__ import annotations

import random
import zlib
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd

from folioex.market_data.base import MarketDataError, PriceData, Profile

ORIGIN = date(2019, 1, 1)
FUNDS = {"SPY", "QQQ", "DIA", "VOO", "SCHD", "VTI", "VYM"}
SECTORS = ("Technology", "Health care", "Financials", "Consumer staples", "Energy")


def business_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def seed_of(symbol: str) -> int:
    return zlib.crc32(symbol.encode())


def path(symbol: str, end: date) -> dict[date, float]:
    """A gently trending random walk from a fixed origin, so any slice of it is always the same."""
    rng = random.Random(seed_of(symbol))
    price = 20 + seed_of(symbol) % 300
    drift = (seed_of(symbol) % 9 - 3) / 10_000
    out: dict[date, float] = {}
    for d in business_days(ORIGIN, end):
        price *= 1 + drift + rng.gauss(0, 0.012)
        out[d] = round(price, 4)
    return out


class FakeProvider:
    def __init__(
        self,
        *,
        offline: bool = False,
        missing: Sequence[str] = (),
        splits: Mapping[str, Sequence[tuple[date, float]]] | None = None,
        no_profile: Sequence[str] = (),
    ) -> None:
        self.offline = offline
        self.missing = set(missing)
        self.splits = {k: list(v) for k, v in (splits or {}).items()}
        self.no_profile = set(no_profile)
        self.price_calls: list[list[str]] = []
        self.profile_calls: list[list[str]] = []

    def prices(self, symbols: Sequence[str], start: date, end: date) -> PriceData:
        self.price_calls.append(list(symbols))
        if self.offline:
            raise MarketDataError("offline")
        closes: dict[str, dict[date, float]] = {}
        dividends: dict[str, list[tuple[date, float]]] = {}
        for s in symbols:
            if s in self.missing:
                continue
            series = {d: p for d, p in path(s, end).items() if d >= start}
            closes[s] = series
            if seed_of(s) % 3 == 0:
                dividends[s] = [
                    (d, round(p * 0.005, 4))
                    for d, p in series.items()
                    if d.month in (3, 6, 9, 12) and 10 <= d.day <= 12 and d.weekday() == 2
                ]
        return PriceData.from_closes(closes, dividends=dividends, splits=self.splits)

    def profiles(self, symbols: Sequence[str]) -> dict[str, Profile]:
        self.profile_calls.append(list(symbols))
        if self.offline:
            raise MarketDataError("offline")
        out: dict[str, Profile] = {}
        for s in symbols:
            if s in self.no_profile or s in self.missing:
                continue
            if s in FUNDS:
                out[s] = Profile(s, name=f"{s} Index Fund", is_fund=True, dividend_yield=0.015)
            else:
                cap = [5e11, 8e10, 5e9, 1e9][seed_of(s) % 4]
                out[s] = Profile(
                    s,
                    name=f"{s} Inc.",
                    sector=SECTORS[seed_of(s) % len(SECTORS)],
                    industry="Things",
                    market_cap=cap,
                    pe=20.0,
                    dividend_yield=0.01,
                )
        return out


def closes_frame(prices: Mapping[str, Mapping[date, float]]) -> pd.DataFrame:
    return PriceData.from_closes(prices).close


UNIVERSE_NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|X
AAAA|Alpha Analytics Inc. - Common Stock|Q|N|N|100|N|N
BBBB|Beta Bio Corp. - Common Stock|G|N|N|100|N|N
ZTST|Nasdaq Test Stock|G|Y|N|100|N|N
QQET|Big Index Trust ETF|G|N|N|100|Y|N
File Creation Time: 1011202608:00|||||||
"""
UNIVERSE_OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
CCCC|Gamma Corp. Common Stock|N|CCCC|N|100|N|CCCC
DD.B|Delta Holdings Class B|N|DD.B|N|100|N|DD.B
EEEE|Epsilon Fund|P|EEEE|Y|100|N|EEEE
FFFF|Foxtrot Mining|A|FFFF|N|100|N|FFFF
GGGG|Golf Cboe Thing|Z|GGGG|N|100|N|GGGG
HH$A|Hotel Preferred|N|HH$A|N|100|N|HH$A
IIII|India Test Inc|N|IIII|N|100|Y|IIII
File Creation Time: 1011202608:00|||||||
"""


class FakeUniverseSource:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def fetch(self) -> tuple[str, str]:
        self.calls += 1
        if self.fail:
            raise MarketDataError("down")
        return UNIVERSE_NASDAQ, UNIVERSE_OTHER
