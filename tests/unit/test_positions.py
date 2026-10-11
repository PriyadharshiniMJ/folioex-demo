"""FIFO lots, realized gains and splits (criteria A3, A8)."""

from datetime import date

import pytest

from folioex.analytics.positions import build_positions
from folioex.domain.models import Transaction, TxnType
from folioex.errors import IssueKind


def txn(
    d: date,
    kind: TxnType,
    symbol: str | None = None,
    qty: float = 0.0,
    price: float | None = None,
    net: float = 0.0,
    commission: float = 0.0,
) -> Transaction:
    return Transaction(
        d,
        kind,
        kind.value.title(),
        symbol,
        qty,
        price,
        "USD" if price else None,
        net - commission,
        commission,
        net,
        None,
        "t.csv",
        0,
    )


D1, D2, D3, D4 = date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 4, 1)


def test_fifo_matches_hand_computed_example() -> None:
    """T-A3: buy 10 @ 100, buy 10 @ 120, sell 15 @ 130, $1 commission each."""
    book = build_positions(
        [
            txn(D1, TxnType.BUY, "AAA", 10, 100, net=-1001, commission=-1),
            txn(D2, TxnType.BUY, "AAA", 10, 120, net=-1201, commission=-1),
            txn(D3, TxnType.SELL, "AAA", -15, 130, net=1949, commission=-1),
        ]
    )
    pos = book.positions["AAA"]
    assert book.realized[0].gain == pytest.approx(347.50)
    assert pos.shares == pytest.approx(5)
    assert pos.cost == pytest.approx(600.50)
    assert pos.cost / pos.shares == pytest.approx(120.10)
    assert pos.realized == pytest.approx(347.50)


def test_partial_fills_consume_lots_in_order() -> None:
    book = build_positions(
        [
            txn(D1, TxnType.BUY, "AAA", 4, 10, net=-40),
            txn(D2, TxnType.SELL, "AAA", -1, 15, net=15),
            txn(D2, TxnType.SELL, "AAA", -1, 15, net=15),
        ]
    )
    assert [round(r.gain, 2) for r in book.realized] == [5.0, 5.0]
    assert book.positions["AAA"].shares == 2


def test_overselling_never_goes_negative() -> None:
    book = build_positions(
        [txn(D1, TxnType.BUY, "AAA", 2, 10, net=-20), txn(D2, TxnType.SELL, "AAA", -5, 12, net=60)]
    )
    assert book.positions["AAA"].shares == 0
    assert book.realized[0].quantity == 2
    assert book.realized[0].gain == pytest.approx(60 * 2 / 5 - 20)


def test_file_split_rescales_lots_and_keeps_cost() -> None:
    """T-A8: 10 shares bought for $2,000; 2-for-1 split → 20 shares, still $2,000."""
    book = build_positions(
        [
            txn(D1, TxnType.BUY, "AAA", 10, 200, net=-2000),
            txn(D2, TxnType.SPLIT, "AAA", 10),
        ]
    )
    pos = book.positions["AAA"]
    assert (pos.shares, pos.cost) == (20, 2000)
    assert pos.lots[0].quantity == 20


def test_market_split_applies_when_the_file_has_none() -> None:
    book = build_positions(
        [txn(D1, TxnType.BUY, "AAA", 10, 200, net=-2000)],
        market_splits={"AAA": [(D2, 3.0)]},
        date_format="%d/%m/%Y",
    )
    assert book.positions["AAA"].shares == 30
    applied = [i for i in book.issues if i.kind is IssueKind.SPLIT_APPLIED]
    assert applied[0].context == {"symbol": "AAA", "ratio": "3-for-1", "date": "03/02/2025"}


def test_market_split_is_not_applied_twice_when_the_file_records_it() -> None:
    book = build_positions(
        [
            txn(D1, TxnType.BUY, "AAA", 10, 200, net=-2000),
            txn(D2, TxnType.SPLIT, "AAA", 20),
        ],
        market_splits={"AAA": [(D2, 3.0)]},
    )
    assert book.positions["AAA"].shares == 30
    assert not [i for i in book.issues if i.kind is IssueKind.SPLIT_APPLIED]


def test_splits_before_owning_the_stock_do_nothing() -> None:
    book = build_positions(
        [txn(D3, TxnType.BUY, "AAA", 10, 50, net=-500)], market_splits={"AAA": [(D1, 4.0)]}
    )
    assert book.positions["AAA"].shares == 10


def test_transfers_move_shares_without_cash() -> None:
    book = build_positions(
        [
            txn(D1, TxnType.TRANSFER_IN, "AAA", 10, 50),
            txn(D2, TxnType.TRANSFER_OUT, "AAA", -4),
        ]
    )
    pos = book.positions["AAA"]
    assert (pos.shares, pos.cost) == (6, 300)
    assert book.realized == []


def test_transfer_without_a_price_uses_the_market_price_and_says_so() -> None:
    book = build_positions(
        [txn(D1, TxnType.TRANSFER_IN, "AAA", 10)], price_on=lambda s, d: 42.0, date_format="%d/%m/%Y"
    )
    assert book.positions["AAA"].cost == 420
    issue = next(i for i in book.issues if i.kind is IssueKind.TRANSFER_COST_ESTIMATED)
    assert issue.context == {"symbol": "AAA", "date": "02/01/2025"}


def test_income_is_tracked_per_symbol() -> None:
    book = build_positions(
        [
            txn(D1, TxnType.BUY, "AAA", 1, 10, net=-10),
            txn(D2, TxnType.DIVIDEND, "AAA", net=5),
            txn(D2, TxnType.WITHHOLDING, "AAA", net=-0.75),
            txn(D3, TxnType.FEE, "AAA", net=-0.1),
        ]
    )
    pos = book.positions["AAA"]
    assert (pos.dividends, pos.withholding, pos.fees) == (5, -0.75, -0.1)
    assert pos.dividends_net == pytest.approx(4.15)


def test_last_trade_price_follows_later_splits() -> None:
    book = build_positions(
        [txn(D1, TxnType.BUY, "AAA", 10, 200, net=-2000), txn(D4, TxnType.SPLIT, "AAA", 10)]
    )
    assert book.last_trade["AAA"] == (D1, 100.0)
