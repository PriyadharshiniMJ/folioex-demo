"""Holdings tab in a real browser: bundled sample data, fake market (criteria U1, U2, U4, U6, U7)."""

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import header_alignments, load_sample, open_tab
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]


def show_holdings(page: Page) -> None:
    load_sample(page)
    open_tab(page, "holdings")
    page.locator("#holdings-body").wait_for(state="visible")
    page.wait_for_selector("#holdings-grid .ag-row")


def test_holdings_tab_shows_cards_charts_and_table(page: Page) -> None:
    show_holdings(page)
    kpis = page.locator("#holdings-kpis").inner_text()
    assert "Account value" in kpis and "Total gain" in kpis and "Dividends" in kpis
    for graph in ("holdings-treemap", "holdings-sectors", "holdings-sizes", "holdings-drivers"):
        page.wait_for_selector(f"#{graph} .main-svg")
    assert set(header_alignments(page, "#holdings-grid")) == {"center"}
    assert page.locator("#holdings-grid .ag-center-cols-container .ag-row").count() >= 10
    assert page.locator("#holdings-asof").inner_text().startswith("as of ")
    assert_no_technical_text(page.locator("#panel-holdings").inner_text())


def test_best_and_weakest_cards_find_rows_in_the_table(page: Page) -> None:
    show_holdings(page)
    best = page.locator("#holdings-best .fx-perf-row")
    worst = page.locator("#holdings-worst .fx-perf-row")
    assert 1 <= best.count() <= 3 and worst.count() <= 3
    assert "not a prediction" in page.locator("#holdings-best").inner_text()
    symbol = best.first.locator(".fx-tk").inner_text()
    best.first.click()
    page.wait_for_selector(f"#holdings-grid .ag-row-selected[row-id='{symbol}']")


def test_explain_describes_the_portfolio(page: Page) -> None:
    show_holdings(page)
    page.locator("#panel-holdings .fx-explain-btn").click()
    panel = page.locator("#explain-panel")
    panel.wait_for(state="visible")
    text = panel.inner_text()
    assert "Your account is worth $" in text
    assert_no_technical_text(text)


def test_phone_width_has_no_sideways_scroll_with_data(page: Page) -> None:
    show_holdings(page)
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(400)
    scroll, client = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert scroll <= client


def test_charts_keep_their_size_after_switching_theme(page: Page) -> None:
    show_holdings(page)
    page.wait_for_timeout(800)
    measure = (
        "['holdings-treemap','holdings-sectors','holdings-sizes','holdings-drivers']"
        ".map(id => Math.round(document.getElementById(id).getBoundingClientRect().height))"
    )
    before = page.evaluate(measure)
    page.locator("#theme-toggle").click()
    page.wait_for_function("document.documentElement.getAttribute('data-theme') === 'dark'")
    page.wait_for_timeout(1500)
    after = page.evaluate(measure)
    assert all(h >= 150 for h in after), after
    assert after == before


def test_table_numbers_are_never_cut_off(page: Page) -> None:
    show_holdings(page)
    truncated = page.evaluate(
        "[...document.querySelectorAll('#holdings-grid .ag-cell.fx-num')]"
        ".filter(c => c.offsetParent && c.scrollWidth > c.clientWidth + 1).map(c => c.innerText)"
    )
    assert truncated == []


def test_gains_and_losses_are_coloured_in_the_table(page: Page) -> None:
    show_holdings(page)
    page.wait_for_selector("#holdings-grid [col-id=gain].fx-up, #holdings-grid [col-id=gain].fx-down")
    colours = page.eval_on_selector_all(
        "#holdings-grid [col-id=gain].ag-cell",
        "els => els.map(e => [e.classList.contains('fx-up'), getComputedStyle(e).color])",
    )
    plain = page.eval_on_selector("#holdings-grid [col-id=shares].ag-cell", "e => getComputedStyle(e).color")
    green = [colour for up, colour in colours if up]
    assert green and all(colour != plain for colour in green)  # a gain is coloured, not plain text
