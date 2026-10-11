"""Dividend income, best/weakest over a period and the full performance view (criteria A9, A10, A13)."""

from datetime import date

import pytest

from folioex.analytics.income import dividend_income
from folioex.analytics.performance import performance_view
from folioex.analytics.returns import resolve_period
from folioex.analytics.valuation import build_daily
from folioex.config import Settings
from folioex.domain.models import Transaction, TxnType
from folioex.ingestion.pipeline import Upload
from folioex.jobs.processing import process
from folioex.market_data.base import PriceData
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from tests.fakes import FakeProvider


def txn(
    d: date,
    kind: TxnType,
    symbol: str | None = None,
    qty: float = 0.0,
    price: float | None = None,
    net: float = 0.0,
) -> Transaction:
    return Transaction(d, kind, kind.value.title(), symbol, qty, price, None, net, 0.0, net, None, "t.csv", 0)


INCOME = [
    txn(date(2024, 3, 15), TxnType.DIVIDEND, "AAA", net=10),
    txn(date(2024, 3, 15), TxnType.WITHHOLDING, "AAA", net=-1.5),
    txn(date(2024, 6, 15), TxnType.DIVIDEND, "BBB", net=4),
    txn(date(2024, 6, 15), TxnType.FEE, "BBB", net=-0.1),
    txn(date(2025, 3, 15), TxnType.DIVIDEND, "AAA", net=12),
    txn(date(2025, 3, 20), TxnType.FEE, net=-5),
]


def test_dividend_totals_agree_every_way_they_are_cut() -> None:
    """A9: by month, by year and by stock all sum to the same totals; account fees aren't dividend fees."""
    inc = dividend_income(INCOME, date(2024, 1, 1), date(2025, 12, 31), include_start=True)
    assert (inc.gross, inc.tax, inc.fees, inc.net) == (26, -1.5, -0.1, pytest.approx(24.4))
    for cut in (inc.by_month, inc.by_year, inc.by_stock):
        assert sum(c.net for c in cut) == pytest.approx(inc.net)
    assert [(y.year, round(y.net, 2), y.yoy) for y in inc.by_year] == [
        (2024, 12.4, None),
        (2025, 12.0, pytest.approx(12 / 12.4 - 1)),
    ]
    aaa = next(s for s in inc.by_stock if s.symbol == "AAA")
    assert (aaa.payments, aaa.gross, aaa.tax) == (2, 22, -1.5)
    assert sum(s.share for s in inc.by_stock) == pytest.approx(1)


def test_dividend_window_is_respected() -> None:
    inc = dividend_income(INCOME, date(2025, 1, 1), date(2025, 12, 31), include_start=False)
    assert inc.net == 12


@pytest.fixture(scope="module")
def sample_data():  # type: ignore[no-untyped-def]
    from folioex.config import load_settings

    settings = load_settings()
    return settings, process(
        sample_uploads(), settings, NullProgress(), market=CachedMarketData(FakeProvider(), None)
    )


def test_view_numbers_are_consistent(sample_data) -> None:  # type: ignore[no-untyped-def]
    settings, data = sample_data
    view = performance_view(data, settings, period="All", benchmark="SPY")
    b = view.breakdown
    assert (
        b.start_value + b.added + b.price_gains + b.dividends + b.withholding + b.fees + b.interest_other
        == pytest.approx(b.end_value, abs=0.01)
    )
    assert view.window.from_inception
    assert view.xirr is not None
    assert view.bench_twr is not None
    assert view.excess == pytest.approx(view.twr - view.bench_twr)
    assert b.end_value == pytest.approx(data.holdings.account_value, rel=1e-6)
    assert view.added == pytest.approx(data.holdings.net_deposits)


def test_short_periods_have_no_yearly_figure(sample_data) -> None:  # type: ignore[no-untyped-def]
    settings, data = sample_data
    view = performance_view(data, settings, period="3M", benchmark="SPY")
    assert view.xirr is None
    assert view.window.days < 365


def test_period_best_and_weakest_include_sold_stocks(sample_data) -> None:  # type: ignore[no-untyped-def]
    settings, data = sample_data
    view = performance_view(data, settings, period="All", benchmark="SPY")
    symbols = [p.symbol for p in view.best + view.worst]
    assert len(set(symbols)) == len(symbols) and 0 < len(view.best) <= 3
    assert all(p.ret is not None for p in view.best + view.worst)


def test_unknown_benchmark_is_handled(sample_data) -> None:  # type: ignore[no-untyped-def]
    settings, data = sample_data
    view = performance_view(data, settings, period="1Y", benchmark="NOPE")
    assert view.bench_twr is None and view.excess is None


def test_period_return_hand_example() -> None:
    """T-A10: start 1000, buy 1000 halfway, dividends 20, end 2200 → gain 220 on average capital 1500."""
    from folioex.analytics.performance import period_performers

    days = [date(2025, 1, 1), date(2025, 1, 11), date(2025, 1, 21)]  # the buy is exactly halfway
    led = [
        txn(days[0], TxnType.DEPOSIT, net=5000),
        txn(days[0], TxnType.BUY, "AAA", 10, 100, net=-1000),
        txn(days[1], TxnType.BUY, "AAA", 10, 100, net=-1000),
        txn(days[2], TxnType.DIVIDEND, "AAA", net=20),
    ]
    prices = PriceData.from_closes({"AAA": {days[0]: 100, days[1]: 100, days[2]: 110}})
    daily = build_daily(led, prices)
    window = resolve_period("Custom", daily.value.index, custom=(date(2025, 1, 2), days[2]))
    assert window.start == days[0]
    best, worst = period_performers(led, daily, window, min_days=0, min_capital=0, count=3, names={})
    aaa = (best + worst)[0]
    assert aaa.gain == pytest.approx(220)
    assert aaa.ret == pytest.approx(220 / 1500, rel=1e-3)


def test_tiny_or_brief_positions_are_left_out_of_period_rankings() -> None:
    from folioex.analytics.performance import period_performers

    d = [date(2025, 1, 1), date(2025, 4, 1), date(2025, 4, 30)]  # NEW is held only 29 days
    led = [
        txn(d[0], TxnType.DEPOSIT, net=5000),
        txn(d[0], TxnType.BUY, "BIG", 10, 100, net=-1000),
        txn(d[0], TxnType.BUY, "TINY", 1, 20, net=-20),
        txn(d[1], TxnType.BUY, "NEW", 1, 500, net=-500),
    ]
    prices = PriceData.from_closes(
        {s: {d[0]: p, d[1]: p, d[2]: p * 1.1} for s, p in (("BIG", 100), ("TINY", 20), ("NEW", 500))}
    )
    daily = build_daily(led, prices)
    window = resolve_period("Custom", daily.value.index, custom=(d[0], d[2]))
    best, worst = period_performers(led, daily, window, min_days=90, min_capital=100, count=3, names={})
    assert [p.symbol for p in best + worst] == ["BIG"]


def test_sold_positions_are_tagged() -> None:
    from folioex.analytics.performance import period_performers

    d = [date(2025, 1, 1), date(2025, 2, 15), date(2025, 4, 30)]
    led = [
        txn(d[0], TxnType.DEPOSIT, net=5000),
        txn(d[0], TxnType.BUY, "OLD", 10, 100, net=-1000),
        txn(d[1], TxnType.SELL, "OLD", -10, 90, net=900),
    ]
    prices = PriceData.from_closes({"OLD": {d[0]: 100, d[1]: 90, d[2]: 95}})
    daily = build_daily(led, prices)
    window = resolve_period("Custom", daily.value.index, custom=(d[0], d[2]))
    best, worst = period_performers(led, daily, window, min_days=30, min_capital=100, count=3, names={})
    old = (best + worst)[0]
    assert old.sold and old.gain == pytest.approx(-100)


def test_uploads_without_market_data_still_give_a_view(settings: Settings) -> None:
    from tests.builders import buy, deposit, make_csv

    raw = make_csv(
        [deposit("02/01/2025", 1000), buy("03/01/2025", "AAA", 2, 100)], sections=("Transaction History",)
    )
    data = process(
        [Upload("a.csv", raw)],
        settings,
        NullProgress(),
        market=CachedMarketData(FakeProvider(offline=True), None),
    )
    view = performance_view(data, settings, period="All", benchmark="SPY")
    assert view.bench_twr is None
    assert view.breakdown.end_value == pytest.approx(data.holdings.account_value)
