"""Performance tab in a real browser (criteria U1, U2, U3, U4, U6, U7, A11)."""

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import header_alignments, load_sample, open_tab
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]
GRAPHS = (
    "perf-value",
    "perf-bench-chart",
    "perf-waterfall",
    "perf-div-month",
    "perf-div-year",
    "perf-top-payers",
    "perf-realized",
    "perf-contrib",
)


def show_performance(page: Page) -> None:
    load_sample(page)
    open_tab(page, "performance")
    page.locator("#performance-body").wait_for(state="visible")
    page.wait_for_selector("#perf-kpis .fx-kpi")


def kpi_text(page: Page) -> str:
    return page.locator("#perf-kpis").inner_text()


def test_everything_renders(page: Page) -> None:
    show_performance(page)
    text = kpi_text(page)
    assert "Return" in text and "vs S&P 500" in text and "Yearly return" in text
    for graph in GRAPHS:
        page.wait_for_selector(f"#{graph} .main-svg")
    assert page.locator("#perf-heatmap tbody tr").count() >= 1
    page.wait_for_selector("#perf-div-stock-grid .ag-row")
    assert set(header_alignments(page, "#panel-performance")) == {"center"}
    assert "Total" in page.locator("#perf-div-stock-grid").inner_text()
    assert_no_technical_text(page.locator("#panel-performance").inner_text())


def test_short_period_and_benchmark_switch(page: Page) -> None:
    show_performance(page)
    note_1y = page.locator("#perf-period-note").inner_text()
    page.locator("#perf-period").get_by_text("3M", exact=True).click()
    page.wait_for_function(f"document.querySelector('#perf-period-note').innerText !== {note_1y!r}")
    assert "1 year or more" in kpi_text(page)
    page.select_option("#perf-bench", "QQQ")
    page.wait_for_function("document.querySelector('#perf-bench-title').innerText.includes('Nasdaq-100')")
    assert "vs Nasdaq-100" in kpi_text(page)


def test_custom_range_and_running_dividends(page: Page) -> None:
    show_performance(page)
    assert page.locator("#perf-custom-wrap").is_hidden()
    page.locator("#perf-period").get_by_text("📅 Custom").click()
    page.locator("#perf-custom-wrap").wait_for(state="visible")
    page.locator("#perf-div-view").get_by_text("Running total").click()
    page.wait_for_function(
        "document.querySelector('#perf-div-month .js-plotly-plot').data[0].type === 'scatter'"
    )


def test_explain_quotes_the_return(page: Page) -> None:
    show_performance(page)
    page.locator("#panel-performance .fx-explain-btn").click()
    panel = page.locator("#explain-panel")
    panel.wait_for(state="visible")
    text = panel.inner_text()
    assert "Over the last year your investments returned" in text
    assert_no_technical_text(text)


def test_charts_survive_theme_switch_and_phone_width(page: Page) -> None:
    show_performance(page)
    for graph in GRAPHS:
        page.wait_for_selector(f"#{graph} .main-svg")
    measure = (
        f"{list(GRAPHS)!r}.map(id => Math.round(document.getElementById(id).getBoundingClientRect().height))"
    )
    before = page.evaluate(measure)
    page.locator("#theme-toggle").click()
    page.wait_for_timeout(1500)
    assert page.evaluate(measure) == before
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(500)
    scroll, client = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert scroll <= client


def test_insight_rows_use_the_full_width(page: Page) -> None:
    show_performance(page)
    widths = page.eval_on_selector_all(
        "#perf-insights .fx-issue", "els => els.map(e => e.getBoundingClientRect().width)"
    )
    assert widths and min(widths) > 300, widths
