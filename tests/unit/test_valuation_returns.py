"""Daily values, time- and money-weighted returns, periods, benchmark and gain breakdown (A4-A8, A11)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from folioex.analytics.attribution import breakdown
from folioex.analytics.benchmark import shadow
from folioex.analytics.returns import (
    Window,
    annualize,
    monthly_returns,
    resolve_period,
    twr,
    twr_series,
    xirr,
)
from folioex.analytics.valuation import build_daily
from folioex.domain.models import Transaction, TxnType
from folioex.market_data.base import PriceData


def txn(
    d: date,
    kind: TxnType,
    symbol: str | None = None,
    qty: float = 0.0,
    price: float | None = None,
    net: float = 0.0,
) -> Transaction:
    return Transaction(d, kind, kind.value.title(), symbol, qty, price, None, net, 0.0, net, None, "t.csv", 0)


def series(values: list[float], start: date = date(2025, 1, 1)) -> pd.Series:
    idx = pd.DatetimeIndex([pd.Timestamp(start + timedelta(days=i)) for i in range(len(values))])
    return pd.Series(values, index=idx, dtype=float)


def test_twr_hand_example() -> None:
    """T-A4: deposit 1000 → 1100 → deposit 500, value 1650: TWR = 1.10 × 1150/1100 − 1 = 15%."""
    value, flows = series([1000, 1100, 1650]), series([1000, 0, 500])
    assert twr(value, flows, 0, 2) == pytest.approx(0.15)
    assert list(twr_series(value, flows, 0, 2).round(6)) == [0.0, 0.1, 0.15]


def test_twr_skips_days_before_there_is_any_money() -> None:
    value, flows = series([0, 0, 1000, 1100]), series([0, 0, 1000, 0])
    assert twr(value, flows, 0, 3) == pytest.approx(0.10)


def test_xirr_one_year_ten_percent() -> None:
    """T-A5: −1000 on 01/01/2025, +1100 on 01/01/2026 → 10.00%."""
    assert xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)]) == pytest.approx(0.10, abs=1e-6)


def test_xirr_without_a_sign_change_is_none() -> None:
    assert xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), -5.0)]) is None
    assert xirr([(date(2025, 1, 1), 1000.0)]) is None


def test_only_periods_of_a_year_or_more_are_annualized() -> None:
    """A11."""
    assert annualize(0.10, 200) is None
    assert annualize(0.21, 730) == pytest.approx(0.1, abs=1e-3)


def dates(first: date, last: date) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in pd.bdate_range(first, last)])


@pytest.mark.parametrize(
    ("code", "start"),
    [
        ("1M", date(2026, 9, 9)),
        ("3M", date(2026, 7, 9)),
        ("YTD", date(2025, 12, 31)),
        ("1Y", date(2025, 10, 9)),
    ],
)
def test_period_windows(code: str, start: date) -> None:
    idx = dates(date(2024, 1, 2), date(2026, 10, 9))
    w = resolve_period(code, idx)
    assert w.end == date(2026, 10, 9)
    assert w.start <= start and (start - w.start).days <= 3
    assert not w.clamped


def test_periods_longer_than_history_are_clamped() -> None:
    idx = dates(date(2025, 3, 3), date(2026, 10, 9))
    w = resolve_period("3Y", idx)
    assert (w.start, w.clamped, w.from_inception) == (date(2025, 3, 3), True, True)
    assert resolve_period("All", idx).from_inception


def test_custom_window() -> None:
    idx = dates(date(2024, 1, 2), date(2026, 10, 9))
    w = resolve_period("Custom", idx, custom=(date(2025, 1, 1), date(2025, 6, 30)))
    assert (w.start, w.end) == (date(2024, 12, 31), date(2025, 6, 30))


def test_monthly_returns_chain_daily_returns() -> None:
    idx = pd.DatetimeIndex(["2025-01-30", "2025-01-31", "2025-02-03", "2025-02-28"])
    value = pd.Series([100.0, 110.0, 99.0, 108.9], index=idx)
    flows = pd.Series([100.0, 0.0, 0.0, 0.0], index=idx)
    months = monthly_returns(value, flows, Window(0, 3, date(2025, 1, 30), date(2025, 2, 28), False, True))
    assert [(m.year, m.month) for m in months] == [(2025, 1), (2025, 2)]
    assert months[0].ret == pytest.approx(0.10)
    assert months[1].ret == pytest.approx(108.9 / 110 - 1)  # (99/110) × (108.9/99)


def test_shadow_benchmark_hand_example() -> None:
    """T-A7: $1,000 into SPY at $500, $1,000 at $400; SPY now $450 → 4.5 units = $2,025."""
    value, flows = series([1000, 2000, 2000]), series([1000, 1000, 0])
    bench = series([500, 400, 450])
    result = shadow(bench, value, flows, 0, 2)
    assert result.values.iloc[-1] == pytest.approx(2025)
    assert result.capped == []


def test_shadow_caps_a_withdrawal_it_cannot_cover() -> None:
    value, flows = series([1000, 1000, 0]), series([1000, 0, -3000])
    result = shadow(series([100, 50, 50]), value, flows, 0, 2)
    assert result.values.iloc[-1] == 0
    assert result.capped == [date(2025, 1, 3)]


def ledger() -> list[Transaction]:
    d = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)]
    return [
        txn(d[0], TxnType.DEPOSIT, net=1000),
        txn(d[0], TxnType.BUY, "AAA", 10, 50, net=-500),
        txn(d[1], TxnType.DIVIDEND, "AAA", net=10),
        txn(d[1], TxnType.WITHHOLDING, "AAA", net=-1.5),
        txn(d[2], TxnType.FEE, net=-2),
        txn(d[2], TxnType.DEPOSIT, net=500),
        txn(d[3], TxnType.INTEREST, net=0.4),
    ]


PRICES = PriceData.from_closes(
    {"AAA": {date(2025, 1, 2): 50, date(2025, 1, 3): 52, date(2025, 1, 6): 55, date(2025, 1, 7): 60}}
)


def test_daily_values() -> None:
    daily = build_daily(ledger(), PRICES)
    assert [round(v, 2) for v in daily.value] == [1000, 1028.5, 1556.5, 1606.9]
    assert list(daily.flows) == [1000, 0, 500, 0]
    assert daily.net_deposits.iloc[-1] == 1500
    assert daily.cash.iloc[-1] == pytest.approx(1000 - 500 + 10 - 1.5 - 2 + 500 + 0.4)


def test_historical_value_before_a_split_is_the_real_value() -> None:
    """T-A8: 10 shares at $200 before a 2-for-1 split; Yahoo shows that day as $100 → still worth $2,000."""
    d0, d1 = date(2025, 1, 2), date(2025, 1, 6)
    prices = PriceData.from_closes({"AAA": {d0: 100, d1: 105}}, splits={"AAA": [(date(2025, 1, 3), 2.0)]})
    led = [
        txn(d0, TxnType.DEPOSIT, net=2000),
        txn(d0, TxnType.BUY, "AAA", 10, 200, net=-2000),
        txn(date(2025, 1, 3), TxnType.SPLIT, "AAA", 10),
    ]
    daily = build_daily(led, prices)
    assert daily.value.iloc[0] == pytest.approx(2000)
    assert daily.value.iloc[-1] == pytest.approx(20 * 105)
    assert daily.shares["AAA"].iloc[-1] == 20


def test_gain_breakdown_adds_up() -> None:
    led = ledger()
    daily = build_daily(led, PRICES)
    window = resolve_period("All", daily.value.index)
    b = breakdown(led, daily, window)
    assert b.start_value == 0 and b.added == 1500
    assert (b.dividends, b.withholding, b.fees, b.interest_other) == (10, -1.5, -2, 0.4)
    assert b.price_gains == pytest.approx(10 * (60 - 50))
    total = b.start_value + b.added + b.price_gains + b.dividends + b.withholding + b.fees + b.interest_other
    assert total == pytest.approx(b.end_value)
