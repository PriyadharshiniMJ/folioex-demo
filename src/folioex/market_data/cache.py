"""Market data through an on-disk cache, with calm fallbacks when the internet or Yahoo is unavailable.

Only public market data is cached (prices, dividends, splits, company details), never the investor's files.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from folioex.config import CacheCfg
from folioex.errors import Issue, IssueKind, Severity
from folioex.formatting import display_date
from folioex.market_data.base import MarketDataError, MarketDataProvider, PriceData, Profile
from folioex.progress import NullProgress, Progress

logger = logging.getLogger("folioex.market")

Status = Literal["live", "offline", "unavailable"]
_COVER_SLACK = timedelta(days=7)  # a cached series starting a few days late (weekend, holiday) still covers
_BATCH = 10


@dataclass(slots=True)
class MarketSnapshot:
    prices: PriceData
    profiles: dict[str, Profile]
    status: Status
    as_of: date | None
    issues: list[Issue] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> MarketSnapshot:
        return cls(PriceData.empty(), {}, "live", None)


class CachedMarketData:
    def __init__(
        self, provider: MarketDataProvider, cfg: CacheCfg | None, *, date_format: str = "%d/%m/%Y"
    ) -> None:
        self.provider = provider
        self.cfg = cfg
        self.date_format = date_format

    # ---- public ----

    def load(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        progress: Progress | None = None,
        benchmarks: Sequence[str] = (),
    ) -> MarketSnapshot:
        progress = progress or NullProgress()
        held = list(dict.fromkeys(symbols))
        extra = [b for b in dict.fromkeys(benchmarks) if b not in held]

        progress.stage("fetch_prices")
        held_data, held_offline, held_failed = self._prices(held, start, end, progress)
        progress.stage("fetch_benchmark")
        bench_data, bench_offline, bench_failed = self._prices(extra, start, end, None)
        progress.stage("fetch_dividends_splits")  # they arrive with the prices
        prices = PriceData.combine([*held_data, *bench_data])
        progress.stage("fetch_profiles")
        profiles = self._profiles(held, progress)
        self._purge()

        offline = held_offline or bench_offline
        failed = held_failed or bench_failed
        found = set(prices.symbols())
        missing = [s for s in held if s not in found]
        issues: list[Issue] = []
        status: Status = "live"
        if failed and not found:
            status = "unavailable"
            issues.append(Issue(IssueKind.MARKET_DATA_UNAVAILABLE, Severity.WARNING))
        elif failed or offline:
            status = "offline"
            as_of = prices.last_date()
            if as_of is not None:
                issues.append(
                    Issue(
                        IssueKind.MARKET_DATA_OFFLINE,
                        Severity.WARNING,
                        {"date": display_date(as_of, self.date_format)},
                    )
                )
        return MarketSnapshot(prices, profiles, status, prices.last_date(), issues, missing)

    # ---- prices ----

    def _prices(
        self, symbols: list[str], start: date, end: date, progress: Progress | None
    ) -> tuple[list[PriceData], bool, bool]:
        """Returns (data, fell back to saved prices because offline, some symbols got nothing at all)."""
        if not symbols:
            return [], False, False
        parts, stale, to_fetch = self._from_cache(symbols, start)
        done = len(parts)
        if progress:
            progress.sub(done, len(symbols), "stocks")
        offline = failed = False
        for i in range(0, len(to_fetch), _BATCH):
            batch = to_fetch[i : i + _BATCH]
            fetched = self._fetch(batch, start, end)
            if fetched is None:  # the source is down: fall back to whatever we saved before
                saved = [stale[s] for s in batch if s in stale]
                parts += saved
                offline |= bool(saved)
                failed |= len(saved) < len(batch)
            else:
                parts += fetched
            done += len(batch)
            if progress:
                progress.sub(done, len(symbols), "stocks")
        return parts, offline, failed

    def _from_cache(
        self, symbols: list[str], start: date
    ) -> tuple[list[PriceData], dict[str, PriceData], list[str]]:
        """Fresh cached data, stale cached data (kept as a fallback), and the symbols to fetch."""
        ttl = self.cfg.price_ttl_hours * 3600 if self.cfg else 0
        fresh: list[PriceData] = []
        stale: dict[str, PriceData] = {}
        to_fetch: list[str] = []
        for s in symbols:
            cached = self._read_prices(s)
            if cached is not None and self._fresh(s, "prices", ttl) and self._covers(cached, s, start):
                fresh.append(cached)
                continue
            to_fetch.append(s)
            if cached is not None:
                stale[s] = cached
        return fresh, stale, to_fetch

    def _fetch(self, batch: list[str], start: date, end: date) -> list[PriceData] | None:
        """One provider call. None means the source couldn't be reached."""
        try:
            data = self.provider.prices(batch, start, end)
        except MarketDataError:
            logger.warning(
                "Market data unavailable for %d symbols; using saved prices where possible", len(batch)
            )
            return None
        except Exception:
            logger.exception("Unexpected problem fetching market data")
            return None
        parts = []
        for s in data.symbols():
            part = _only(data, s)
            parts.append(part)
            self._write_prices(s, part)
        return parts

    def _covers(self, data: PriceData, symbol: str, start: date) -> bool:
        series = data.series(symbol)
        return not series.empty and series.index[0].date() <= start + _COVER_SLACK

    def _fresh(self, symbol: str, kind: str, ttl_seconds: float) -> bool:
        path = self._path(kind, symbol)
        return path is not None and path.exists() and time.time() - path.stat().st_mtime < ttl_seconds

    def _path(self, kind: str, symbol: str) -> Path | None:
        if self.cfg is None:
            return None
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in symbol)
        return self.cfg.dir / kind / f"{safe}.csv"

    def _read_prices(self, symbol: str) -> PriceData | None:
        path = self._path("prices", symbol)
        if path is None or not path.exists():
            return None
        try:
            frame = pd.read_csv(path, parse_dates=["date"])
        except (OSError, ValueError, KeyError):
            logger.warning("Ignoring an unreadable cache file for %s", symbol)
            return None
        frame = frame.set_index("date")

        def day(ts: object) -> date:
            return pd.Timestamp(str(ts)).date()

        closes = {symbol: {day(ts): float(v) for ts, v in frame["close"].dropna().items()}}
        adj = {symbol: {day(ts): float(v) for ts, v in frame["adj_close"].dropna().items()}}
        dividends = [(day(ts), float(v)) for ts, v in frame["dividend"].items() if v and v > 0]
        splits = [(day(ts), float(v)) for ts, v in frame["split"].items() if v and v > 0]
        return PriceData.from_closes(closes, adj=adj, dividends={symbol: dividends}, splits={symbol: splits})

    def _write_prices(self, symbol: str, data: PriceData) -> None:
        path = self._path("prices", symbol)
        if path is None:
            return
        frame = pd.DataFrame({"close": data.close[symbol], "adj_close": data.adj_close[symbol]})
        frame["dividend"] = 0.0
        frame["split"] = 0.0
        for d, v in data.dividends.get(symbol, []):
            frame.loc[pd.Timestamp(d), "dividend"] = v
        for d, v in data.splits.get(symbol, []):
            frame.loc[pd.Timestamp(d), "split"] = v
        frame.index.name = "date"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.sort_index().to_csv(path)
        except OSError:
            logger.warning("Couldn't save market data for %s to the cache", symbol)

    # ---- profiles ----

    def _profiles(self, symbols: list[str], progress: Progress) -> dict[str, Profile]:
        saved = self._read_profiles()
        ttl = self.cfg.profile_ttl_days * 86400 if self.cfg else 0
        now = time.time()
        out = {
            s: Profile(**saved[s]["profile"]) for s in symbols if s in saved and now - saved[s]["at"] < ttl
        }
        wanted = [s for s in symbols if s not in out]
        progress.sub(len(out), len(symbols), "stocks")
        if wanted:
            try:
                fetched = self.provider.profiles(wanted)
            except MarketDataError:
                fetched = {}
                logger.warning("Company details unavailable; using saved ones where possible")
            except Exception:
                fetched = {}
                logger.exception("Unexpected problem fetching company details")
            for s in wanted:
                if s in fetched:
                    out[s] = fetched[s]
                    saved[s] = {"at": now, "profile": _profile_dict(fetched[s])}
                elif s in saved:
                    out[s] = Profile(**saved[s]["profile"])
            self._write_profiles(saved)
        progress.sub(len(symbols), len(symbols), "stocks")
        return out

    def _profiles_path(self) -> Path | None:
        return None if self.cfg is None else self.cfg.dir / "profiles.json"

    def _read_profiles(self) -> dict[str, dict[str, Any]]:
        path = self._profiles_path()
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_profiles(self, saved: dict[str, dict[str, Any]]) -> None:
        path = self._profiles_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(saved, indent=1), encoding="utf-8")
        except OSError:
            logger.warning("Couldn't save company details to the cache")

    # ---- housekeeping ----

    def _purge(self) -> None:
        """Keep the cache under its size cap by removing the oldest files first."""
        if self.cfg is None or not self.cfg.dir.exists():
            return
        files = sorted((f for f in self.cfg.dir.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        cap = self.cfg.max_mb * 1024 * 1024
        for f in files:
            if total <= cap:
                break
            total -= f.stat().st_size
            try:
                f.unlink()
            except OSError:
                logger.warning("Couldn't remove an old cache file")


def _only(data: PriceData, symbol: str) -> PriceData:
    return PriceData(
        close=data.close[[symbol]].dropna(),
        adj_close=data.adj_close[[symbol]].dropna()
        if symbol in data.adj_close
        else data.close[[symbol]].dropna(),
        dividends={symbol: data.dividends.get(symbol, [])},
        splits={symbol: data.splits.get(symbol, [])},
    )


def _profile_dict(p: Profile) -> dict[str, object]:
    return {
        "symbol": p.symbol,
        "name": p.name,
        "sector": p.sector,
        "industry": p.industry,
        "market_cap": p.market_cap,
        "is_fund": p.is_fund,
        "pe": p.pe,
        "dividend_yield": p.dividend_yield,
    }
