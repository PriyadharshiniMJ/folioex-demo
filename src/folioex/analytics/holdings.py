"""What you own today: one row per holding plus account totals.

Account value = holdings at the latest close + cash.
Net deposits = deposits − withdrawals (including opening cash).
A holding without a market price is valued at its last trade price and labelled as such.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from folioex.analytics.positions import PositionBook
from folioex.domain.models import EXTERNAL_FLOW_TYPES, Transaction, TxnType
from folioex.errors import Issue, IssueKind, Severity
from folioex.market_data.base import PriceData, Profile

FUNDS = "Funds (ETF)"
UNCLASSIFIED = "Unclassified"
CAP_ORDER = ("Mega", "Large", "Mid", "Small", FUNDS, UNCLASSIFIED)
_EPS = 1e-9


@dataclass(slots=True)
class HoldingRow:
    symbol: str
    name: str
    sector: str
    cap_bucket: str
    shares: float
    cost: float
    avg_cost: float
    price: float
    price_date: date | None
    prev_close: float | None
    value: float
    weight: float
    day_change: float
    day_pct: float | None
    gain: float
    gain_pct: float | None
    dividends_net: float
    yoc: float | None
    priced_from: Literal["market", "last trade"]
    first_date: date | None


@dataclass(slots=True)
class HoldingsView:
    rows: list[HoldingRow]
    cash: float
    invested: float
    account_value: float
    net_deposits: float
    total_gain: float
    total_gain_pct: float | None
    unrealized: float
    realized: float
    day_change: float
    dividends_net: float
    as_of: date | None
    issues: list[Issue] = field(default_factory=list)


_CAP_FLOORS = ((200e9, "Mega"), (10e9, "Large"), (2e9, "Mid"))


def cap_bucket(profile: Profile | None) -> str:
    if profile is None or (profile.market_cap is None and not profile.is_fund):
        return UNCLASSIFIED
    if profile.is_fund:
        return FUNDS
    cap = profile.market_cap or 0.0
    return next((name for floor, name in _CAP_FLOORS if cap >= floor), "Small")


def _sector(profile: Profile | None) -> str:
    if profile is None:
        return UNCLASSIFIED
    if profile.is_fund:
        return FUNDS
    return profile.sector or UNCLASSIFIED


def build_holdings(
    transactions: Sequence[Transaction],
    book: PositionBook,
    prices: PriceData,
    profiles: Mapping[str, Profile],
) -> HoldingsView:
    issues: list[Issue] = []
    as_of = prices.last_date()
    rows: list[HoldingRow] = []
    for symbol, pos in book.positions.items():
        shares = pos.shares
        if shares <= _EPS:
            continue
        last = prices.last(symbol)
        price_date: date | None
        source: Literal["market", "last trade"]
        if last is not None:
            price_date, price = last
            prev, source = prices.previous_close(symbol), "market"
        else:
            price_date, price = book.last_trade.get(symbol, (None, pos.cost / shares))
            prev, source = None, "last trade"
            issues.append(Issue(IssueKind.TICKER_NOT_FOUND, Severity.WARNING, {"symbol": symbol}))
        value = shares * price
        cost = pos.cost
        fresh = prev is not None and price_date == as_of
        day_change = shares * (price - prev) if fresh and prev is not None else 0.0
        trailing = sum(
            t.net
            for t in transactions
            if t.symbol == symbol
            and t.type is TxnType.DIVIDEND
            and as_of is not None
            and t.date > as_of - timedelta(days=365)
        )
        profile = profiles.get(symbol)
        rows.append(
            HoldingRow(
                symbol=symbol,
                name=(profile.name if profile and profile.name else symbol),
                sector=_sector(profile),
                cap_bucket=cap_bucket(profile),
                shares=shares,
                cost=cost,
                avg_cost=cost / shares,
                price=price,
                price_date=price_date,
                prev_close=prev,
                value=value,
                weight=0.0,
                day_change=day_change,
                day_pct=(price / prev - 1) if fresh and prev else None,
                gain=value - cost,
                gain_pct=(value / cost - 1) if cost > _EPS else None,
                dividends_net=pos.dividends_net,
                yoc=(trailing / cost) if cost > _EPS and trailing else None,
                priced_from=source,
                first_date=pos.first_date,
            )
        )

    cash = sum(t.net for t in transactions)
    invested = sum(r.value for r in rows)
    account = invested + cash
    for r in rows:
        r.weight = r.value / account if account > _EPS else 0.0
    rows.sort(key=lambda r: -r.value)
    net_deposits = sum(t.net for t in transactions if t.type in EXTERNAL_FLOW_TYPES)
    total_gain = account - net_deposits
    if net_deposits <= _EPS and transactions:
        issues.append(Issue(IssueKind.NO_NET_DEPOSITS, Severity.INFO))
    unclassified = sum(1 for r in rows if r.sector == UNCLASSIFIED)
    if unclassified:
        issues.append(Issue(IssueKind.PROFILE_MISSING, Severity.INFO, {"count": unclassified}))
    dividends_net = sum(p.dividends_net for p in book.positions.values())
    return HoldingsView(
        rows=rows,
        cash=cash,
        invested=invested,
        account_value=account,
        net_deposits=net_deposits,
        total_gain=total_gain,
        total_gain_pct=total_gain / net_deposits if net_deposits > _EPS else None,
        unrealized=sum(r.gain for r in rows),
        realized=sum(g.gain for g in book.realized),
        day_change=sum(r.day_change for r in rows),
        dividends_net=dividends_net,
        as_of=as_of,
        issues=issues,
    )
