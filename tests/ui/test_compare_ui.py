"""Compare tab: lines, table, limits, wording and Explain (criteria C1–C9, E2, U2, U10)."""

from pathlib import Path

import dash_ag_grid as dag
import pandas as pd
import pytest
from dash import html

from folioex.analytics.performance import performance_view
from folioex.config import Settings, load_settings
from folioex.dataset import Dataset
from folioex.errors import IssueKind, MessageCatalog
from folioex.formatting import compact_money
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.market_data.universe import Universe, parse_universe
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from folioex.ui.app import create_app
from folioex.ui.tabs import compare as cv
from tests.fakes import UNIVERSE_NASDAQ as NASDAQ
from tests.fakes import UNIVERSE_OTHER as OTHER
from tests.fakes import FakeProvider
from tests.fakes import FakeUniverseSource as Source
from tests.helpers import assert_no_technical_text, text_of, walk

MARKET = CachedMarketData(FakeProvider(), None)


@pytest.fixture(scope="module")
def data() -> Dataset:
    return process(sample_uploads(), load_settings(), NullProgress(), market=MARKET)


def screen(
    data: Dataset,
    settings: Settings,
    catalog: MessageCatalog,
    *,
    market: CachedMarketData = MARKET,
    **kw: object,
) -> cv.CompareScreen:
    args: dict[str, object] = {
        "holdings": [],
        "wishlist": [],
        "period": "1Y",
        "benchmark": "SPY",
        "show_portfolio": True,
        "show_benchmark": False,
        "slots": {},
        "theme": "light",
    } | kw
    return cv.compare_screen(data, market, settings, catalog, **args)  # type: ignore[arg-type]


def held(data: Dataset, n: int = 2) -> list[str]:
    return [r.symbol for r in sorted(data.holdings.rows, key=lambda r: -r.value)][:n]


def test_the_portfolio_line_matches_the_performance_tab(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    s = screen(data, settings, catalog)
    portfolio = next(r for r in s.rows if r["type"] == "Whole portfolio")
    view = performance_view(data, settings, period="1Y", benchmark="SPY")
    assert portfolio["ret"] == pytest.approx(view.twr)  # one definition of "how I did", everywhere


def test_holdings_and_wishlist_become_lines_and_rows(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    mine = held(data, 2)
    s = screen(data, settings, catalog, holdings=mine, wishlist=["WISH"])
    assert [r["type"] for r in s.rows] == ["Whole portfolio", "Holding", "Holding", "Wishlist"]
    assert {r["ticker"] for r in s.rows[1:]} == {*mine, "WISH"}
    start = s.lines[0].index.index[0]
    assert all(pd.Timestamp(trace.x[0]) == start for trace in s.figure.data)  # all start together
    assert all(abs(trace.y[0] - 100) < 1e-9 for trace in s.figure.data)  # and at $100
    assert s.count_text == "3 of 8 picked"


def test_lines_are_told_apart_by_more_than_colour(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    s = screen(data, settings, catalog, holdings=held(data, 1), wishlist=["WISH"], show_benchmark=True)
    by_name = {t.name: t for t in s.figure.data}
    assert by_name["Whole portfolio"].line.width >= 3
    assert by_name["WISH"].line.dash == "dash" and by_name[held(data, 1)[0]].line.dash in (None, "solid")
    assert by_name["S&P 500"].line.dash == "dot"
    assert s.figure.layout.height


def test_a_ticker_keeps_its_colour(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    first = screen(data, settings, catalog, wishlist=["AAA", "BBB", "CCC"])
    second = screen(data, settings, catalog, wishlist=["AAA", "CCC"], slots=first.slots)
    assert second.slots["AAA"] == first.slots["AAA"] and second.slots["CCC"] == first.slots["CCC"]
    colour = {t.name: t.line.color for t in first.figure.data}
    after = {t.name: t.line.color for t in second.figure.data}
    assert colour["AAA"] == after["AAA"] and colour["CCC"] == after["CCC"]


def test_toggling_the_portfolio_and_benchmark(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    without = screen(data, settings, catalog, wishlist=["WISH"], show_portfolio=False)
    assert [r["type"] for r in without.rows] == ["Wishlist"]
    with_bench = screen(data, settings, catalog, wishlist=["WISH"], show_benchmark=True, benchmark="QQQ")
    assert any(r["type"] == "Benchmark" and r["name"] == "Nasdaq-100" for r in with_bench.rows)
    assert "Nasdaq-100" in {t.name for t in with_bench.figure.data}


def test_stars_only_on_picks_and_only_with_two(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    s = screen(data, settings, catalog, wishlist=["AAA", "BBB"], show_benchmark=True)
    picks = [r for r in s.rows if r["type"] == "Wishlist"]
    assert all("stars" in r for r in s.rows)
    assert any("ret" in r["stars"] for r in picks)
    assert all(r["stars"] == [] for r in s.rows if r["type"] != "Wishlist")
    alone = screen(data, settings, catalog, wishlist=["AAA"])
    assert all(r["stars"] == [] for r in alone.rows)


def test_funds_have_no_pe_and_values_come_from_company_details(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    s = screen(data, settings, catalog, wishlist=["VTI", "ZZZ"])
    fund = next(r for r in s.rows if r["ticker"] == "VTI")
    stock = next(r for r in s.rows if r["ticker"] == "ZZZ")
    assert fund["pe"] is None and stock["pe"] == 20.0
    assert stock["cap"] and stock["name"] == "ZZZ Inc."


def test_period_changes_the_numbers(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    one_year = screen(data, settings, catalog, wishlist=["AAA"], period="1Y")
    three_months = screen(data, settings, catalog, wishlist=["AAA"], period="3M")
    a = next(r for r in one_year.rows if r["ticker"] == "AAA")
    b = next(r for r in three_months.rows if r["ticker"] == "AAA")
    assert a["ret"] != b["ret"]
    assert a["annual"] is not None and b["annual"] is None  # a yearly figure only for a year or more


def test_never_more_than_the_limit(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog, wishlist=[f"T{i}" for i in range(12)])
    assert len([r for r in s.rows if r["type"] == "Wishlist"]) == settings.compare.max_tickers
    assert s.count_text == "8 of 8 picked"


def test_an_unknown_ticker_gets_a_plain_note_and_the_rest_still_draws(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    market = CachedMarketData(FakeProvider(missing=["NOPE"]), None)
    s = screen(data, settings, catalog, market=market, wishlist=["NOPE", "FINE"])
    text = text_of(html.Div(s.notes))
    assert "NOPE" in text and "couldn't find market prices" in text
    assert "FINE" in {r["ticker"] for r in s.rows}
    assert "NOPE" not in {r["ticker"] for r in s.rows}
    assert_no_technical_text(text)


def test_no_internet_still_shows_the_portfolio(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    market = CachedMarketData(FakeProvider(offline=True), None)
    s = screen(data, settings, catalog, market=market, holdings=held(data, 1), wishlist=["WISH"])
    types = [r["type"] for r in s.rows]
    assert "Whole portfolio" in types and "Holding" in types and "Wishlist" not in types
    text = text_of(html.Div(s.notes))
    assert "market data service" in text
    assert_no_technical_text(text)


def test_a_late_listing_is_flagged(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    window_start = screen(data, settings, catalog, wishlist=["AAA"]).lines[0].index.index[0]
    late = CachedMarketData(_LateProvider(window_start), None)
    s = screen(data, settings, catalog, market=late, wishlist=["NEWB"])
    assert any(line.since is not None for line in s.lines if line.key == "NEWB")
    assert "only has prices from" in text_of(html.Div(s.notes))


class _LateProvider(FakeProvider):
    def __init__(self, first_day: object) -> None:
        super().__init__()
        self.first_day = first_day

    def prices(self, symbols, start, end):  # type: ignore[no-untyped-def]
        import pandas as pd

        out = super().prices(symbols, start, end)
        cut = pd.Timestamp(self.first_day) + pd.Timedelta(days=60)  # type: ignore[arg-type]
        out.close.loc[out.close.index < cut, :] = float("nan")
        out.adj_close.loc[out.adj_close.index < cut, :] = float("nan")
        return out


def test_table_columns_and_disclaimer(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    headers = [c.header for c in cv.COLUMNS]
    assert headers == [
        "Ticker",
        "Name",
        "Type",
        "Return",
        "Yearly",
        "Ups & downs",
        "Biggest drop",
        "Sharpe",
        "Dividend yield",
        "P/E",
        "Market cap",
    ]
    assert all(c.tip for c in cv.COLUMNS[3:])  # every number column explains itself
    layout_text = text_of(cv.layout(settings, catalog))
    assert catalog.text("compare_note") in layout_text
    assert "★" in layout_text


def test_layout_has_the_controls(settings: Settings, catalog: MessageCatalog) -> None:
    ids = {str(getattr(c, "id", None)) for c in walk(cv.layout(settings, catalog))}
    for wanted in (
        "cmp-holdings",
        "cmp-wish",
        "cmp-period",
        "cmp-portfolio",
        "cmp-bench",
        "cmp-bench-show",
        "cmp-chart",
        "cmp-grid",
        "compare-body",
    ):
        assert wanted in ids, wanted
    grid = next(c for c in walk(cv.layout(settings, catalog)) if isinstance(c, dag.AgGrid))
    assert all(d["headerClass"] == "fx-center" for d in grid.columnDefs)  # U2


def test_option_lists_stop_offering_picks_at_the_limit(
    data: Dataset, settings: Settings, catalog: MessageCatalog, tmp_path: Path
) -> None:
    uni = Universe(Source(), None)
    assert parse_universe(NASDAQ, OTHER)  # the fixture text is valid
    mine = held(data, 2)
    opts = cv.option_lists(
        data, uni, catalog, settings, holdings=mine, wishlist=["AAAA"], search="b", limit=8
    )
    assert any(o["value"] == "BBBB" for o in opts.wishlist)
    assert opts.note == ""
    full = cv.option_lists(
        data,
        uni,
        catalog,
        settings,
        holdings=mine,
        wishlist=["AAAA", *[f"X{i}" for i in range(5)]],
        search="b",
        limit=8,
    )
    assert all(o["value"] != "BBBB" for o in full.wishlist)
    assert all(o.get("disabled") for o in full.holdings if o["value"] not in mine)
    assert "up to 8" in full.note
    assert_no_technical_text(full.note)


def test_typed_tickers_can_be_used(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    uni = Universe(Source(), None)
    opts = cv.option_lists(data, uni, catalog, settings, holdings=[], wishlist=[], search="abcd", limit=8)
    typed = [o for o in opts.wishlist if o["value"] == "ABCD"]
    assert len(typed) == 1 and "typed" in typed[0]["label"]
    # nothing that isn't a plausible ticker is offered
    junk = cv.option_lists(
        data, uni, catalog, settings, holdings=[], wishlist=[], search="not a ticker!", limit=8
    )
    assert all(o["value"] != "NOT A TICKER!" for o in junk.wishlist)


def test_without_the_stock_list_search_still_works_by_typing(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    dead = Universe(Source(fail=True), None)
    opts = cv.option_lists(data, dead, catalog, settings, holdings=[], wishlist=[], search="msft", limit=8)
    assert [o["value"] for o in opts.wishlist] == ["MSFT"]
    assert "NYSE and Nasdaq" in opts.universe_note
    assert_no_technical_text(opts.universe_note)


def test_explain_quotes_the_numbers_on_screen(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    kwargs = {
        "holdings": held(data, 1),
        "wishlist": ["AAA", "BBB"],
        "period": "1Y",
        "benchmark": "SPY",
        "show_portfolio": True,
        "show_benchmark": False,
    }
    content = cv.explain(data, MARKET, catalog, settings, **kwargs)  # type: ignore[arg-type]
    s = screen(data, settings, catalog, **kwargs)
    text = text_of(content)
    portfolio = next(r for r in s.rows if r["type"] == "Whole portfolio")
    assert f"{portfolio['ret'] * 100:+.1f}%".replace("-", "−") in text
    assert "of the 3 stocks you picked" in text
    assert "$100" in text
    assert catalog.text("explain_footer") in text
    assert_no_technical_text(text)
    other = text_of(cv.explain(data, MARKET, catalog, settings, **(kwargs | {"period": "3M"})))  # type: ignore[arg-type]
    assert other != text


def test_explain_without_picks_says_what_to_do(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    text = text_of(
        cv.explain(
            data,
            MARKET,
            catalog,
            settings,
            holdings=[],
            wishlist=[],
            period="1Y",
            benchmark="SPY",
            show_portfolio=True,
            show_benchmark=False,
        )
    )
    assert "Pick" in text
    assert_no_technical_text(text)


def test_compact_money() -> None:
    assert compact_money(3.4e12) == "$3.4T"
    assert compact_money(8e10) == "$80.0B"
    assert compact_money(1.234e9) == "$1.2B"
    assert compact_money(9.5e8) == "$950M"
    assert compact_money(12_000) == "$12,000"
    assert compact_money(None) == "—"


def test_the_app_wires_up_the_compare_tab(settings: Settings, catalog: MessageCatalog) -> None:
    app = create_app(settings, catalog, MARKET, Universe(Source(), None))
    ids = {str(getattr(c, "id", None)) for c in walk(app.layout)}
    assert {"cmp-chart", "cmp-grid", "cmp-holdings"} <= ids
    assert IssueKind.COMPARE_LIMIT in IssueKind
