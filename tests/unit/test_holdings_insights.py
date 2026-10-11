"""Best/weakest since purchase, allocation and the holdings insight rules (criteria A10, E1)."""

from datetime import date

import pytest

from folioex.analytics.allocation import by_cap, by_sector
from folioex.analytics.holdings import HoldingRow, HoldingsView
from folioex.analytics.insights import HOLDINGS_RULES, Insight, holdings_insights
from folioex.analytics.performers import since_purchase, split_best_worst
from folioex.config import InsightsCfg, PerformersCfg
from folioex.errors import MessageCatalog
from tests.helpers import assert_no_technical_text

TODAY = date(2026, 10, 9)


def row(
    symbol: str,
    value: float,
    gain_pct: float,
    sector: str = "Technology",
    cap: str = "Mega",
    first: date = date(2025, 1, 1),
) -> HoldingRow:
    cost = value / (1 + gain_pct)
    return HoldingRow(
        symbol=symbol,
        name=f"{symbol} Inc.",
        sector=sector,
        cap_bucket=cap,
        shares=10,
        cost=cost,
        avg_cost=cost / 10,
        price=value / 10,
        price_date=TODAY,
        prev_close=value / 10,
        value=value,
        weight=0.0,
        day_change=0.0,
        day_pct=0.0,
        gain=value - cost,
        gain_pct=gain_pct,
        dividends_net=0.0,
        yoc=None,
        priced_from="market",
        first_date=first,
    )


def holdings(rows: list[HoldingRow], cash: float = 0.0) -> HoldingsView:
    invested = sum(r.value for r in rows)
    account = invested + cash
    for r in rows:
        r.weight = r.value / account
    return HoldingsView(
        rows=sorted(rows, key=lambda r: -r.value),
        cash=cash,
        invested=invested,
        account_value=account,
        net_deposits=account,
        total_gain=0.0,
        total_gain_pct=0.0,
        unrealized=0.0,
        realized=0.0,
        day_change=0.0,
        dividends_net=0.0,
        as_of=TODAY,
        issues=[],
    )


def test_best_and_weakest_since_purchase_never_overlap() -> None:
    rows = [row(s, 1000, g) for s, g in zip("ABCDEFG", [0.5, -0.2, 0.1, 0.3, -0.05, 0.9, 0.0], strict=True)]
    best, worst = since_purchase(holdings(rows), PerformersCfg(), TODAY)
    assert [p.symbol for p in best] == ["F", "A", "D"]
    assert [p.symbol for p in worst] == ["B", "E", "G"]


@pytest.mark.parametrize(("n", "best_n", "worst_n"), [(5, 3, 2), (2, 1, 1), (1, 1, 0), (0, 0, 0)])
def test_few_holdings_are_split_without_repeats(n: int, best_n: int, worst_n: int) -> None:
    best, worst = split_best_worst(list(range(n)), key=float, count=3)
    assert (len(best), len(worst)) == (best_n, worst_n)
    assert not set(best) & set(worst)


def test_tiny_or_brand_new_positions_are_left_out() -> None:
    rows = [row("OLD", 1000, 0.2), row("NEW", 1000, 0.9, first=date(2026, 10, 1)), row("TINY", 50, 3.0)]
    best, worst = since_purchase(holdings(rows), PerformersCfg(min_days=30, min_avg_capital=100), TODAY)
    assert [p.symbol for p in best + worst] == ["OLD"]


def test_sector_and_size_breakdowns() -> None:
    rows = [
        row("A", 600, 0.1),
        row("B", 300, -0.1, sector="Energy", cap="Large"),
        row("C", 100, 0.0, cap="Large"),
    ]
    sectors = by_sector(holdings(rows).rows)
    assert [(s.name, round(s.weight, 2)) for s in sectors] == [("Technology", 0.7), ("Energy", 0.3)]
    sizes = by_cap(holdings(rows).rows)
    assert [(s.name, round(s.weight, 2)) for s in sizes] == [("Mega", 0.6), ("Large", 0.4)]


def keys(insights: list[Insight]) -> set[str]:
    return {i.key for i in insights}


def test_concentration_and_idle_cash_rules_fire() -> None:
    rows = [row("BIG", 700, 0.1), row("S1", 200, 0.0, sector="Energy"), row("S2", 50, -0.3, sector="Energy")]
    found = holdings_insights(holdings(rows, cash=100), by_sector(holdings(rows).rows), InsightsCfg())
    assert {
        "position_concentration",
        "sector_concentration",
        "idle_cash",
        "big_losers",
        "best_sector",
    } <= keys(found)
    concentration = next(i for i in found if i.key == "position_concentration")
    assert concentration.severity == "warning" and concentration.context["symbol"] == "BIG"


def test_calm_portfolio_has_only_positive_or_neutral_notes() -> None:
    rows = [
        row(s, 100, 0.05, sector=sec)
        for s, sec in zip("ABCDEFGHIJ", ["T", "H", "F", "C", "E"] * 2, strict=True)
    ]
    found = holdings_insights(holdings(rows), by_sector(holdings(rows).rows), InsightsCfg())
    assert not [i for i in found if i.severity == "warning"]


def test_every_rule_has_plain_english_wording(catalog: MessageCatalog) -> None:
    for key in HOLDINGS_RULES:
        template = catalog.insight_template(key)
        assert template, key
    rows = [row("BIG", 700, 0.6), row("S1", 200, 0.0, sector="Energy"), row("S2", 50, -0.3, sector="Energy")]
    for insight in holdings_insights(holdings(rows, cash=100), by_sector(holdings(rows).rows), InsightsCfg()):
        text = catalog.insight(insight)
        assert text and "{" not in text
        assert_no_technical_text(text)
