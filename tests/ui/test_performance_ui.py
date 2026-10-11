"""Performance tab: cards, charts, dividends, insights and Explain (criteria U1, U2, A11, A13)."""

import dash_ag_grid as dag
import pytest
from dash import html

from folioex.config import Settings, load_settings
from folioex.dataset import Dataset
from folioex.errors import MessageCatalog
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from folioex.ui.app import create_app
from folioex.ui.components.kpi import render_kpis
from folioex.ui.tabs import performance as pv
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text, text_of, walk


@pytest.fixture(scope="module")
def data() -> Dataset:
    return process(
        sample_uploads(), load_settings(), NullProgress(), market=CachedMarketData(FakeProvider(), None)
    )


def screen(data: Dataset, settings: Settings, catalog: MessageCatalog, **kw: object) -> pv.PerformanceScreen:
    args = {"period": "1Y", "benchmark": "SPY", "custom": None, "running": False, "theme": "light"} | kw
    return pv.performance_screen(data, settings, catalog, **args)  # type: ignore[arg-type]


def test_summary_cards(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog)
    assert [k.label for k in s.kpis] == [
        "Return",
        "Yearly return",
        "vs S&P 500",
        "Money added",
        "Dividends",
        "Realized gains",
    ]
    text = text_of(html.Div(render_kpis(s.kpis)))
    assert "pts" in text
    assert_no_technical_text(text)
    assert [k.label for k in s.dividend_kpis] == [
        "Dividends this period",
        "Last 12 months",
        "Share of your gain",
        "Growth vs last year",
    ]


def test_short_period_explains_why_there_is_no_yearly_figure(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    yearly = next(k for k in screen(data, settings, catalog, period="3M").kpis if k.label == "Yearly return")
    assert yearly.value == "—" and "1 year or more" in yearly.sub


def test_benchmark_choice_changes_labels(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog, benchmark="QQQ")
    assert s.bench_title == "You vs the Nasdaq-100"
    assert any(k.label == "vs Nasdaq-100" for k in s.kpis)


def test_period_note_and_clamping(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    assert "→" in screen(data, settings, catalog).period_note
    clamped = screen(
        data, settings, catalog, period="Custom", custom=(pv.date(2000, 1, 1), pv.date(2022, 1, 1))
    )
    assert "all available" in clamped.period_note


def test_charts_and_tables_are_built(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog, period="All")
    assert len(s.value.data) == 2 and len(s.bench.data) == 2
    assert s.waterfall.data[0].type == "waterfall"
    assert s.waterfall.data[0].x[-1] == "Value now"
    assert len(s.div_month.data) == 3
    assert screen(data, settings, catalog, period="All", running=True).div_month.data[0].type == "scatter"
    assert s.div_year_rows and s.div_stock_rows and s.div_stock_total["ticker"] == "Total"
    total_net = sum(r["net"] for r in s.div_stock_rows)
    assert s.div_stock_total["net"] == pytest.approx(total_net)
    for fig in (
        s.value,
        s.bench,
        s.waterfall,
        s.div_month,
        s.div_year,
        s.top_payers,
        s.realized,
        s.contribution,
    ):
        assert fig.layout.height and fig.layout.height >= 200


def test_heatmap_has_a_cell_for_every_month(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    s = screen(data, settings, catalog, period="All")
    table = s.heatmap
    headers = [str(n.children) for n in walk(table) if isinstance(n, html.Th)]
    assert headers[:13] == [
        "",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    assert "Year" in headers
    assert_no_technical_text(text_of(table))


def test_best_and_weakest_and_insights(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog, period="All")
    best_text = text_of(s.best_card)
    assert "not a prediction" in best_text
    assert s.insights
    for _sev, text in s.insights:
        assert_no_technical_text(text)


def test_explain_matches_the_screen(data: Dataset, settings: Settings, catalog: MessageCatalog) -> None:
    s = screen(data, settings, catalog)
    text = text_of(pv.explain(data, catalog, settings, period="1Y", benchmark="SPY", custom=None))
    ret = next(k for k in s.kpis if k.label == "Return").value.replace("▲ ", "").replace("▼ ", "")
    assert ret in text  # the summary quotes exactly what the card shows (A13)
    assert "isn't financial advice" in text
    assert_no_technical_text(text)


def test_layout_pieces(settings: Settings) -> None:
    layout = create_app(settings).layout
    ids = {str(getattr(n, "id", "")) for n in walk(layout)}
    for wanted in (
        "performance-body",
        "perf-period",
        "perf-custom",
        "perf-bench",
        "perf-kpis",
        "perf-value",
        "perf-bench-chart",
        "perf-best",
        "perf-worst",
        "perf-waterfall",
        "perf-heatmap",
        "perf-div-kpis",
        "perf-div-month",
        "perf-div-view",
        "perf-div-year",
        "perf-top-payers",
        "perf-div-year-grid",
        "perf-div-stock-grid",
        "perf-realized",
        "perf-contrib",
        "perf-insights",
        "benchmark-store",
    ):
        assert wanted in ids, wanted
    grids = [n for n in walk(layout) if isinstance(n, dag.AgGrid) and str(n.id).startswith("perf")]
    assert grids and all(g.defaultColDef["headerClass"] == "fx-center" for g in grids)


def test_partial_years_are_labelled_and_never_compared(data: Dataset, settings: Settings,
                                                       catalog: MessageCatalog) -> None:  # fmt: skip
    """In a 1-year window the first year is only a few months, so a growth % against it would mislead."""
    s = screen(data, settings, catalog, period="1Y")
    years = s.div_year_rows
    assert years[0]["year"].endswith("(part)") and years[0]["yoy"] is None
    assert years[-1]["year"].endswith("(so far)") and years[-1]["yoy"] is None
    labels = list(s.div_year.data[0].x)
    assert labels[0].endswith("(part)") and "%" not in s.div_year.data[0].text[-1]


def test_full_years_compare_with_the_whole_previous_year(data: Dataset, settings: Settings,
                                                         catalog: MessageCatalog) -> None:  # fmt: skip
    rows = {r["year"]: r for r in screen(data, settings, catalog, period="All").div_year_rows}
    assert rows["2022"]["yoy"] == pytest.approx(rows["2022"]["net"] / rows["2021"]["net"] - 1)


def test_severity_classes_never_clash_with_the_info_marker(data: Dataset, settings: Settings,
                                                           catalog: MessageCatalog) -> None:  # fmt: skip
    from folioex.ui.tabs.holdings import render_insights

    s = screen(data, settings, catalog, period="All")
    classes = [str(getattr(n, "className", "")) for n in walk(render_insights(s.insights))]
    assert not [c for c in classes if "fx-info" in c.split()], classes
