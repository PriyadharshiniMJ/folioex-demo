"""The processing job: every stage in order, market data via the cache, and graceful offline behaviour."""

from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import IssueKind
from folioex.ingestion.pipeline import Upload, ingest
from folioex.jobs.processing import process
from folioex.jobs.stages import STAGES
from folioex.market_data.cache import CachedMarketData
from tests.builders import buy, deposit, make_csv
from tests.fakes import FakeProvider


class Recorder:
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.subs: list[tuple[int, int, str]] = []

    def stage(self, key: str) -> None:
        self.stages.append(key)

    def sub(self, done: int, total: int, unit: str = "") -> None:
        self.subs.append((done, total, unit))


def uploads() -> list[Upload]:
    a = make_csv(
        [deposit("02/01/2025", 1000), buy("03/01/2025", "AAA", 1, 10)], sections=("Transaction History",)
    )
    b = make_csv(
        [deposit("02/01/2026", 100), buy("05/01/2026", "BBB", 2, 20)], sections=("Transaction History",)
    )
    return [Upload("a.csv", a), Upload("b.csv", b)]


def run(settings: Settings, provider: FakeProvider | None = None, rec: Recorder | None = None) -> Dataset:
    market = CachedMarketData(provider or FakeProvider(), None)
    return process(uploads(), settings, rec or Recorder(), market=market)


def test_every_stage_is_reported_once_in_order(settings: Settings) -> None:
    rec = Recorder()
    run(settings, rec=rec)
    assert rec.stages == [s.key for s in STAGES]


def test_reading_and_fetching_report_sub_progress(settings: Settings) -> None:
    rec = Recorder()
    run(settings, rec=rec)
    assert (2, 2, "files") in rec.subs
    assert (2, 2, "stocks") in rec.subs


def test_result_carries_files_market_data_and_holdings(settings: Settings) -> None:
    data = run(settings)
    assert data.ingest.transactions == ingest(uploads(), settings).transactions
    assert {r.symbol for r in data.holdings.rows} == {"AAA", "BBB"}
    assert data.market.status == "live"
    assert all(r.priced_from == "market" for r in data.holdings.rows)
    assert set(data.market.prices.symbols()) >= {"AAA", "BBB", *settings.benchmark.choices}


def test_offline_still_finishes_with_a_plain_message(settings: Settings) -> None:
    data = run(settings, provider=FakeProvider(offline=True))
    assert [i.kind for i in data.analysis_issues() if i.kind is IssueKind.MARKET_DATA_UNAVAILABLE]
    assert {r.priced_from for r in data.holdings.rows} == {"last trade"}


def test_no_transactions_means_no_market_requests(settings: Settings) -> None:
    provider = FakeProvider()
    empty = make_csv([], sections=("Transaction History",))
    data = process([Upload("e.csv", empty)], settings, Recorder(), market=CachedMarketData(provider, None))
    assert provider.price_calls == []
    assert data.holdings.rows == []
