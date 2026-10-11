"""Holdings tab view model, charts, layout and Explain panel (criteria U1, U2, A10, E2, E3)."""

import dash_ag_grid as dag
import pytest
from dash import html

from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import MessageCatalog
from folioex.ingestion.pipeline import Upload
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.ui.app import create_app
from folioex.ui.tabs import holdings as hv
from tests.builders import buy, deposit, dividend, make_csv, sell
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text, text_of, walk

ROWS = [
    deposit("02/01/2024", 20000),
    buy("03/01/2024", "AAA", 40, 100),
    buy("03/01/2024", "BBB", 30, 80),
    buy("03/01/2024", "CCC", 50, 40),
    buy("04/01/2024", "DDD", 20, 60),
    buy("04/01/2024", "EEE", 10, 150),
    buy("05/01/2024", "SPY", 5, 400),
    buy("05/01/2024", "FFF", 25, 30),
    dividend("15/03/2024", "AAA", 12.5),
    sell("10/06/2024", "BBB", 10, 90),
]


def build(settings: Settings, provider: FakeProvider | None = None) -> Dataset:
    raw = make_csv(ROWS, sections=("Transaction History",))
    return process(
        [Upload("2024.csv", raw)],
        settings,
        NullProgress(),
        market=CachedMarketData(provider or FakeProvider(), None),
    )


@pytest.fixture
def data(settings: Settings) -> Dataset:
    return build(settings)


def test_summary_cards(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    assert [k.label for k in screen.kpis] == [
        "Account value",
        "Total gain",
        "Cash",
        "Invested",
        "Unrealized gain",
        "Today",
        "Realized gain",
        "Dividends",
    ]
    account = next(k for k in screen.kpis if k.label == "Account value")
    assert account.value == f"${data.holdings.account_value:,.0f}"
    assert all(k.tip for k in screen.kpis)
    text = text_of(html.Div(hv.render_kpis(screen.kpis)))
    assert "Account value" in text
    assert_no_technical_text(text)


def test_table_has_every_holding_and_centered_headers(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    assert {r["ticker"] for r in screen.table_rows} == {r.symbol for r in data.holdings.rows}
    first = screen.table_rows[0]
    assert set(first) >= {
        "ticker",
        "name",
        "shares",
        "avg_cost",
        "price",
        "value",
        "weight",
        "day_pct",
        "gain",
        "gain_pct",
        "dividends",
        "yoc",
    }
    grid = next(n for n in walk(hv.layout()) if isinstance(n, dag.AgGrid))
    assert all(c["headerClass"] == "fx-center" for c in grid.columnDefs)
    assert grid.getRowId == "params.data.ticker"


def test_charts_cover_every_holding(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    tickers = {r.symbol for r in data.holdings.rows}
    assert tickers <= set(screen.treemap.data[0].labels)
    assert screen.sectors.data[0].type == "pie"
    assert set(screen.drivers.data[0].y) == tickers
    assert screen.sizes.data[0].orientation == "h"


def test_best_and_weakest_lists(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    assert 0 < len(screen.best) <= 3 and len(screen.worst) <= 3
    assert not {p.symbol for p in screen.best} & {p.symbol for p in screen.worst}
    card = hv.render_performers("🏆 Best right now", screen.best, data, "light", catalog)
    text = text_of(card)
    assert "not a prediction" in text
    assert_no_technical_text(text)


def test_insights_are_plain_english(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    assert screen.insights
    for _severity, text in screen.insights:
        assert text
        assert_no_technical_text(text)


def test_dark_theme_recolours_charts(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    light = hv.holdings_screen(data, settings, catalog, theme="light")
    dark = hv.holdings_screen(data, settings, catalog, theme="dark")
    assert light.drivers.layout.font.color != dark.drivers.layout.font.color


def test_offline_shows_a_calm_note(settings: Settings, catalog: MessageCatalog) -> None:
    data = build(settings, FakeProvider(offline=True))
    screen = hv.holdings_screen(data, settings, catalog, theme="light")
    note = text_of(html.Div(screen.market_note))
    assert "couldn't reach the market data service" in note
    assert screen.as_of_text == "prices unavailable · valued at last trade prices"
    assert_no_technical_text(note)


def test_explain_panel_describes_this_portfolio(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    text = text_of(hv.explain(data, catalog, settings))
    assert "In a nutshell" in text
    assert f"${data.holdings.account_value:,.0f}" in text
    assert "isn't financial advice" in text
    assert_no_technical_text(text)


def test_layout_is_wired_into_the_app(settings: Settings) -> None:
    ids = {str(getattr(n, "id", "")) for n in walk(create_app(settings).layout)}
    for wanted in (
        "holdings-body",
        "holdings-kpis",
        "holdings-insights",
        "holdings-best",
        "holdings-worst",
        "holdings-treemap",
        "holdings-sectors",
        "holdings-sizes",
        "holdings-drivers",
        "holdings-grid",
        "holdings-asof",
        "holdings-market-note",
    ):
        assert wanted in ids, wanted


def test_every_chart_carries_its_own_height(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    """A redraw (e.g. switching theme) must never collapse a chart to an empty container's height."""
    screen = hv.holdings_screen(data, settings, catalog, theme="dark")
    for fig in (screen.treemap, screen.sectors, screen.sizes, screen.drivers):
        assert fig.layout.height and fig.layout.height >= 150
    assert screen.drivers.layout.height >= 30 * len(data.holdings.rows)
