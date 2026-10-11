"""Current holdings, account totals and the cash and gain identities (criteria A1, A2, A6, E3)."""

from datetime import date

import pytest
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from folioex.analytics.holdings import build_holdings
from folioex.analytics.positions import build_positions
from folioex.domain.models import EXTERNAL_FLOW_TYPES, Transaction, TxnType
from folioex.errors import IssueKind
from folioex.market_data.base import PriceData, Profile

D = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)]


def txn(
    d: date,
    kind: TxnType,
    symbol: str | None = None,
    qty: float = 0.0,
    price: float | None = None,
    net: float = 0.0,
) -> Transaction:
    return Transaction(d, kind, kind.value.title(), symbol, qty, price, None, net, 0.0, net, None, "t.csv", 0)


LEDGER = [
    txn(D[0], TxnType.DEPOSIT, net=2000),
    txn(D[0], TxnType.BUY, "AAA", 10, 100, net=-1000),
    txn(D[1], TxnType.BUY, "BBB", 5, 50, net=-250),
    txn(D[2], TxnType.DIVIDEND, "AAA", net=8),
    txn(D[2], TxnType.WITHHOLDING, "AAA", net=-1.2),
    txn(D[3], TxnType.SELL, "BBB", -2, 60, net=120),
    txn(D[3], TxnType.INTEREST, net=0.5),
]
PRICES = PriceData.from_closes({"AAA": {D[2]: 110, D[3]: 120}, "BBB": {D[2]: 58, D[3]: 55}})
PROFILES = {
    "AAA": Profile("AAA", name="Alpha Corp", sector="Technology", market_cap=3e11),
    "BBB": Profile("BBB", name="Beta Fund", is_fund=True),
}


def view(ledger: list[Transaction] = LEDGER, prices: PriceData = PRICES, profiles: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    return build_holdings(ledger, build_positions(ledger), prices, PROFILES if profiles is None else profiles)


def test_values_weights_and_totals() -> None:
    v = view()
    rows = {r.symbol: r for r in v.rows}
    assert rows["AAA"].value == 1200 and rows["BBB"].value == 165
    assert v.cash == pytest.approx(2000 - 1000 - 250 + 8 - 1.2 + 120 + 0.5)
    assert v.invested == 1365
    assert v.account_value == pytest.approx(v.cash + 1365)
    assert sum(r.weight for r in v.rows) + v.cash / v.account_value == pytest.approx(1)
    assert [r.symbol for r in v.rows] == ["AAA", "BBB"]  # largest first
    assert v.as_of == D[3]


def test_per_holding_figures() -> None:
    aaa = next(r for r in view().rows if r.symbol == "AAA")
    assert (aaa.shares, aaa.cost, aaa.avg_cost) == (10, 1000, 100)
    assert aaa.gain == 200 and aaa.gain_pct == pytest.approx(0.2)
    assert aaa.day_change == pytest.approx(100) and aaa.day_pct == pytest.approx(120 / 110 - 1)
    assert aaa.dividends_net == pytest.approx(6.8)
    assert aaa.yoc == pytest.approx(8 / 1000)
    assert (aaa.name, aaa.sector, aaa.cap_bucket) == ("Alpha Corp", "Technology", "Mega")
    bbb = next(r for r in view().rows if r.symbol == "BBB")
    assert (bbb.sector, bbb.cap_bucket) == ("Funds (ETF)", "Funds (ETF)")


def test_cash_identity() -> None:
    """A1: final cash = Σ net of every row."""
    v = view()
    assert v.cash == pytest.approx(sum(t.net for t in LEDGER))


def test_gain_identity_on_a_known_ledger() -> None:
    """A2: account − net deposits = realized + unrealized + income − tax − fees ± interest/other."""
    v = view()
    income = sum(
        t.net
        for t in LEDGER
        if t.type in (TxnType.DIVIDEND, TxnType.WITHHOLDING, TxnType.FEE, TxnType.INTEREST, TxnType.OTHER)
    )
    assert v.account_value - v.net_deposits == pytest.approx(v.realized + v.unrealized + income, abs=0.01)
    assert v.total_gain == pytest.approx(v.account_value - 2000)
    assert v.total_gain_pct == pytest.approx(v.total_gain / 2000)


@st.composite
def ledgers(draw: st.DrawFn) -> list[Transaction]:
    days = [date(2024, 1, 1 + i) for i in range(28)]
    cash, shares, out = 0.0, {"AAA": 0.0, "BBB": 0.0}, []
    for d in days:
        action = draw(st.sampled_from(["deposit", "buy", "sell", "div", "fee", "split", "nothing"]))
        sym = draw(st.sampled_from(["AAA", "BBB"]))
        price = draw(st.floats(min_value=1, max_value=500, allow_nan=False))
        if action == "deposit":
            amount = round(draw(st.floats(min_value=10, max_value=5000)), 2)
            out.append(txn(d, TxnType.DEPOSIT, net=amount))
            cash += amount
        elif action == "buy" and cash > price:
            qty = float(draw(st.integers(min_value=1, max_value=max(1, int(cash // price)))))
            cost = round(qty * price, 2)
            out.append(txn(d, TxnType.BUY, sym, qty, price, net=-cost))
            cash -= cost
            shares[sym] += qty
        elif action == "sell" and shares[sym] >= 1:
            qty = float(draw(st.integers(min_value=1, max_value=int(shares[sym]))))
            proceeds = round(qty * price, 2)
            out.append(txn(d, TxnType.SELL, sym, -qty, price, net=proceeds))
            cash += proceeds
            shares[sym] -= qty
        elif action == "div" and shares[sym] > 0:
            out.append(txn(d, TxnType.DIVIDEND, sym, net=round(price / 100, 2)))
        elif action == "fee":
            out.append(txn(d, TxnType.FEE, net=-1.0))
        elif action == "split" and shares[sym] > 0:
            out.append(txn(d, TxnType.SPLIT, sym, shares[sym]))  # 2-for-1
            shares[sym] *= 2
    return out


@given(ledgers(), st.floats(min_value=1, max_value=500), st.floats(min_value=1, max_value=500))
@hsettings(max_examples=150, deadline=None)
def test_cash_and_gain_identities_hold_for_any_valid_ledger(
    ledger: list[Transaction], pa: float, pb: float
) -> None:
    prices = PriceData.from_closes({"AAA": {date(2024, 2, 1): pa}, "BBB": {date(2024, 2, 1): pb}})
    v = build_holdings(ledger, build_positions(ledger), prices, {})
    assert v.cash == pytest.approx(sum(t.net for t in ledger), abs=1e-6)
    assert all(r.shares >= 0 for r in v.rows)
    income = sum(
        t.net
        for t in ledger
        if t.type not in EXTERNAL_FLOW_TYPES | {TxnType.BUY, TxnType.SELL, TxnType.SPLIT}
    )
    assert v.account_value - v.net_deposits == pytest.approx(v.realized + v.unrealized + income, abs=0.02)


def test_missing_prices_fall_back_to_the_last_trade() -> None:
    """E3: a ticker without market data is valued at its last trade price, labelled and reported."""
    prices = PriceData.from_closes({"AAA": {D[3]: 120}})
    v = view(prices=prices)
    bbb = next(r for r in v.rows if r.symbol == "BBB")
    assert (bbb.price, bbb.priced_from) == (60, "last trade")
    assert any(i.kind is IssueKind.TICKER_NOT_FOUND and i.context["symbol"] == "BBB" for i in v.issues)


def test_no_market_data_at_all_still_values_everything() -> None:
    v = view(prices=PriceData.from_closes({}))
    assert {r.priced_from for r in v.rows} == {"last trade"}
    assert v.as_of is None
    assert v.account_value > 0


def test_missing_profiles_are_unclassified_and_counted() -> None:
    v = view(profiles={})
    assert {r.sector for r in v.rows} == {"Unclassified"}
    assert {r.name for r in v.rows} == {"AAA", "BBB"}
    issue = next(i for i in v.issues if i.kind is IssueKind.PROFILE_MISSING)
    assert issue.context == {"count": 2}


def test_no_net_deposits_means_no_percentage() -> None:
    ledger = [txn(D[0], TxnType.DEPOSIT, net=100), txn(D[1], TxnType.WITHDRAWAL, net=-150)]
    v = view(ledger=ledger)
    assert v.total_gain_pct is None
    assert any(i.kind is IssueKind.NO_NET_DEPOSITS for i in v.issues)


def test_sold_out_positions_are_not_holdings() -> None:
    ledger = [*LEDGER, txn(D[3], TxnType.SELL, "BBB", -3, 55, net=165)]
    assert [r.symbol for r in view(ledger=ledger).rows] == ["AAA"]


@pytest.mark.parametrize(
    ("cap", "bucket"), [(3e11, "Mega"), (5e10, "Large"), (5e9, "Mid"), (5e8, "Small"), (None, "Unclassified")]
)
def test_company_size_buckets(cap: float | None, bucket: str) -> None:
    v = view(profiles={"AAA": Profile("AAA", sector="Energy", market_cap=cap)})
    assert next(r for r in v.rows if r.symbol == "AAA").cap_bucket == bucket
