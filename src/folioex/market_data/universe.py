"""The list of NYSE and Nasdaq stocks and ETFs behind the Compare search box.

It comes from Nasdaq Trader's public symbol directories, is kept on disk for a day, and is only fetched when
the Compare tab first needs it. Without internet and without a saved copy the list is simply empty: the
investor can still type any ticker symbol.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from folioex.config import CacheCfg
from folioex.ingestion.symbols import normalize_symbol
from folioex.market_data.base import MarketDataError

logger = logging.getLogger("folioex.market")

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_TIMEOUT = 15
_RETRY_AFTER = 60  # seconds before trying a failed download again
_OTHER_EXCHANGES = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca"}  # the NYSE family
_NO_RESULTS = 30


@dataclass(frozen=True, slots=True)
class Listing:
    symbol: str
    name: str
    exchange: str
    is_fund: bool

    @property
    def label(self) -> str:
        return f"{self.symbol} · {self.name}" + (" (fund)" if self.is_fund else "")


class UniverseSource(Protocol):
    def fetch(self) -> tuple[str, str]:
        """The two directory files as text (Nasdaq-listed, other-listed). Raises MarketDataError."""


class HttpUniverseSource:
    def fetch(self) -> tuple[str, str]:
        try:
            return _download(NASDAQ_URL), _download(OTHER_URL)
        except (OSError, ValueError) as exc:  # URLError, timeouts and bad URLs are all OSError/ValueError
            raise MarketDataError(str(exc)) from exc


def _download(url: str) -> str:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
        return str(response.read().decode("utf-8", errors="replace"))


def _rows(text: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    if any(name not in header for name in required):
        return []
    out = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == len(header) and not cells[0].startswith("File Creation Time"):
            out.append(dict(zip(header, cells, strict=True)))
    return out


def _clean_name(raw: str) -> str:
    return raw.split(" - ", maxsplit=1)[0].strip()


def parse_universe(nasdaq_text: str, other_text: str) -> list[Listing]:
    """Listings sorted by symbol: real NYSE-family and Nasdaq issues, without special share types."""
    found: dict[str, Listing] = {}
    for row in _rows(nasdaq_text, ("Symbol", "Security Name", "Test Issue", "ETF")):
        if row["Test Issue"] == "Y":
            continue
        _add(found, row["Symbol"], row["Security Name"], "Nasdaq", row["ETF"] == "Y")
    for row in _rows(other_text, ("ACT Symbol", "Security Name", "Exchange", "ETF", "Test Issue")):
        exchange = _OTHER_EXCHANGES.get(row["Exchange"])
        if exchange is None or row["Test Issue"] == "Y":
            continue
        _add(found, row["ACT Symbol"], row["Security Name"], exchange, row["ETF"] == "Y")
    return sorted(found.values(), key=lambda item: item.symbol)


def _add(found: dict[str, Listing], raw: str, name: str, exchange: str, is_fund: bool) -> None:
    if "$" in raw:  # preferreds, warrants and the like
        return
    symbol = normalize_symbol(raw, {}, "")
    if symbol and symbol not in found:
        found[symbol] = Listing(symbol, _clean_name(name), exchange, is_fund)


class Universe:
    def __init__(self, source: UniverseSource, cfg: CacheCfg | None) -> None:
        self._source = source
        self._cfg = cfg
        self._lock = threading.Lock()
        self._listings: list[Listing] | None = None
        self._failed_at = 0.0

    @property
    def available(self) -> bool:
        return bool(self._load())

    def search(self, query: str, limit: int = _NO_RESULTS) -> list[Listing]:
        """Best matches first: the exact symbol, symbols starting with the text, then company names."""
        text = query.strip().lower()
        if not text:
            return []
        ranked: list[tuple[int, int, str, Listing]] = []
        for item in self._load():
            symbol = item.symbol.lower()
            if symbol == text:
                rank = 0
            elif symbol.startswith(text):
                rank = 1
            elif text in item.name.lower():
                rank = 2
            else:
                continue
            ranked.append((rank, len(symbol), symbol, item))
        ranked.sort(key=lambda r: r[:3])
        return [r[3] for r in ranked[:limit]]

    def lookup(self, symbol: str) -> Listing | None:
        """A listing from the list already in memory. Never downloads, so it is safe to call anywhere."""
        return next((i for i in self._listings or [] if i.symbol == symbol), None)

    # ---- loading ----

    def _load(self) -> list[Listing]:
        with self._lock:
            if self._listings:
                return self._listings
            saved, fresh = self._read()
            if saved and fresh:
                self._listings = saved
            elif time.monotonic() - self._failed_at > _RETRY_AFTER or self._failed_at == 0.0:
                self._listings = self._download() or saved
            else:
                self._listings = saved
            return self._listings or []

    def _download(self) -> list[Listing]:
        try:
            nasdaq, other = self._source.fetch()
        except MarketDataError:
            logger.warning("Couldn't download the stock list; using the saved one if there is one")
            self._failed_at = time.monotonic()
            return []
        except Exception:
            logger.exception("Unexpected problem downloading the stock list")
            self._failed_at = time.monotonic()
            return []
        listings = parse_universe(nasdaq, other)
        if not listings:
            logger.warning("The downloaded stock list had nothing usable in it")
            self._failed_at = time.monotonic()
            return []
        self._write(listings)
        return listings

    def _read(self) -> tuple[list[Listing], bool]:
        path = self._cfg.dir / "universe.json" if self._cfg else None
        if path is None or not path.exists():
            return [], False
        try:
            data: Mapping[str, object] = json.loads(path.read_text(encoding="utf-8"))
            rows = data["listings"]
            saved_at = float(data["at"])  # type: ignore[arg-type]
            listings = [Listing(str(s), str(n), str(e), bool(f)) for s, n, e, f in rows]  # type: ignore[attr-defined]
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Ignoring an unreadable saved stock list")
            return [], False
        assert self._cfg is not None
        return listings, time.time() - saved_at < self._cfg.universe_ttl_days * 86400

    def _write(self, listings: list[Listing]) -> None:
        if self._cfg is None:
            return
        path = self._cfg.dir / "universe.json"
        payload = {
            "at": time.time(),
            "listings": [[i.symbol, i.name, i.exchange, i.is_fund] for i in listings],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.warning("Couldn't save the stock list to the cache")
