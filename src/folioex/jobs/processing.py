"""The processing job behind the progress bar: read files, fetch market data, then work out the numbers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from folioex.analytics.holdings import build_holdings
from folioex.analytics.positions import build_positions
from folioex.analytics.valuation import build_daily
from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.ingestion.pipeline import Upload, ingest
from folioex.jobs.stages import STAGES, stage_index
from folioex.market_data.cache import CachedMarketData, MarketSnapshot
from folioex.progress import Progress

_LOOKBACK = timedelta(days=7)


def process(
    uploads: Sequence[Upload], settings: Settings, progress: Progress, *, market: CachedMarketData
) -> Dataset:
    result = ingest(uploads, settings, progress=progress)
    if result.transactions:
        snapshot = market.load(
            result.tickers,
            result.transactions[0].date - _LOOKBACK,
            date.today(),
            progress=progress,
            benchmarks=list(settings.benchmark.choices),
        )
    else:
        snapshot = MarketSnapshot.empty()  # nothing to price: don't touch the network
        for stage in STAGES[stage_index("fetch_prices") : stage_index("apply_splits")]:
            progress.stage(stage.key)

    progress.stage("apply_splits")
    progress.stage("build_lots")
    book = build_positions(
        result.transactions,
        market_splits=snapshot.prices.splits,
        price_on=snapshot.prices.price_on,
        date_format=settings.format.date_format,
    )
    progress.stage("replay_ledger")
    progress.stage("daily_valuation")
    daily = build_daily(result.transactions, snapshot.prices)
    progress.stage("realized_gains")
    progress.stage("dividend_income")
    holdings = build_holdings(result.transactions, book, snapshot.prices, snapshot.profiles)
    for key in ("twr", "xirr", "benchmark", "allocation", "insights_quiz"):
        progress.stage(key)
    return Dataset(result, snapshot, book, holdings, daily)
