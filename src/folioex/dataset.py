"""Everything worked out for one session's data: files, market data, positions and holdings."""

from __future__ import annotations

from dataclasses import dataclass

from folioex.analytics.holdings import HoldingsView
from folioex.analytics.positions import PositionBook
from folioex.analytics.valuation import DailySeries
from folioex.errors import Issue, Severity
from folioex.ingestion.pipeline import IngestResult
from folioex.market_data.cache import MarketSnapshot

_ORDER = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass(slots=True)
class Dataset:
    ingest: IngestResult
    market: MarketSnapshot
    book: PositionBook
    holdings: HoldingsView
    daily: DailySeries

    def analysis_issues(self) -> list[Issue]:
        """Issues found after reading the files: market data, splits, transfers, pricing."""
        return [*self.market.issues, *self.book.issues, *self.holdings.issues]

    def all_issues(self) -> list[Issue]:
        return sorted([*self.ingest.all_issues(), *self.analysis_issues()], key=lambda i: _ORDER[i.severity])
