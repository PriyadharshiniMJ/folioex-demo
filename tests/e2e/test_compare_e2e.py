"""Compare tab in a real browser, on the sample data with a fake market (criteria C8, U1, U2, U4, U7, U10)."""

import re

import pytest
from playwright.sync_api import Page

from folioex.errors import default_catalog
from tests.e2e.conftest import header_alignments, load_sample, open_tab
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]


def show_compare(page: Page) -> None:
    load_sample(page)
    open_tab(page, "compare")
    page.locator("#compare-body").wait_for(state="visible")
    page.wait_for_selector("#cmp-grid .ag-row")
    page.wait_for_function("document.querySelector('#cmp-count').textContent === '2 of 8 picked'")
    page.wait_for_load_state("networkidle")  # the starting picks and their options have all arrived


def rows(page: Page) -> list[str]:
    return page.locator("#cmp-grid .ag-center-cols-container .ag-row").all_inner_texts()


def count(page: Page) -> str:
    return page.locator("#cmp-count").inner_text()


def pick_wish(page: Page, query: str, label: str) -> None:
    page.locator("#cmp-wish").click()
    page.keyboard.type(query)
    page.locator(".dash-dropdown-content").get_by_text(label, exact=False).first.click()
    page.keyboard.press("Escape")


def legend_names(page: Page) -> list[str]:
    return page.evaluate(
        "Array.from(document.querySelectorAll('#cmp-chart .legendtext')).map(e => e.textContent)"
    )


def test_portfolio_and_two_holdings_are_shown_to_start(page: Page) -> None:
    show_compare(page)
    assert count(page) == "2 of 8 picked"
    types = [r for r in rows(page)]
    assert len(types) == 3 and "Whole portfolio" in types[0] and types[1].count("Holding") == 1
    page.wait_for_selector("#cmp-chart .main-svg")
    assert "Whole portfolio" in legend_names(page) and len(legend_names(page)) == 3
    assert set(header_alignments(page, "#cmp-grid")) == {"center"}  # U2
    text = page.locator("#panel-compare").inner_text()
    assert default_catalog().text("compare_note") in text  # U10
    assert_no_technical_text(text)


def test_search_and_add_a_wishlist_stock(page: Page) -> None:
    show_compare(page)
    pick_wish(page, "bio", "BBBB")
    page.wait_for_function("document.querySelector('#cmp-count').textContent === '3 of 8 picked'")
    assert any("BBBB" in r and "Wishlist" in r for r in rows(page))
    assert "BBBB" in legend_names(page)
    assert page.locator("#cmp-grid .fx-star").count() >= 1  # two or more picks: a best in column


def test_any_ticker_can_be_typed(page: Page) -> None:
    show_compare(page)
    pick_wish(page, "msft", "Use MSFT (typed)")
    page.wait_for_function("document.querySelector('#cmp-count').textContent === '3 of 8 picked'")
    assert any("MSFT" in r for r in rows(page))


def test_the_ninth_pick_is_not_offered(page: Page) -> None:
    show_compare(page)
    for symbol in ("AAAA", "BBBB", "CCCC", "DD-B", "EEEE", "FFFF"):
        pick_wish(page, symbol, f"{symbol} ·")
    page.wait_for_function("document.querySelector('#cmp-count').textContent === '8 of 8 picked'")
    note = page.locator("#cmp-limit-note").inner_text()
    assert "up to 8" in note
    assert_no_technical_text(note)
    page.locator("#cmp-wish").click()
    page.keyboard.type("QQET")
    page.wait_for_timeout(600)
    assert page.locator(".dash-dropdown-content").get_by_text("QQET", exact=False).count() == 0
    page.keyboard.press("Escape")
    assert len(rows(page)) == 9  # eight picks and the whole portfolio


def test_period_portfolio_and_benchmark_controls(page: Page) -> None:
    show_compare(page)
    before = page.locator("#cmp-period-note").inner_text()
    page.locator("#cmp-period label", has_text="3M").click()
    page.wait_for_function(
        "(old) => document.querySelector('#cmp-period-note').textContent !== old", arg=before
    )
    page.locator("#cmp-bench-show").click()
    page.wait_for_selector("#cmp-grid .ag-row:has-text('benchmark')")
    page.locator("#cmp-portfolio").click()
    page.wait_for_function("!document.querySelector('#cmp-grid').textContent.includes('Whole portfolio')")


def test_the_benchmark_choice_is_shared_with_performance(page: Page) -> None:
    show_compare(page)
    page.select_option("#cmp-bench", "QQQ")
    page.wait_for_function("document.querySelector('#perf-bench').value === 'QQQ'")
    open_tab(page, "performance")
    assert page.locator("#perf-bench").input_value() == "QQQ"
    page.select_option("#perf-bench", "DIA")
    open_tab(page, "compare")
    page.wait_for_function("document.querySelector('#cmp-bench').value === 'DIA'")


def test_explain_quotes_the_portfolio_and_picks(page: Page) -> None:
    show_compare(page)
    page.locator("#panel-compare .fx-explain-btn").click()
    panel = page.locator("#explain-panel")
    panel.wait_for(state="visible")
    text = panel.inner_text()
    assert "your whole portfolio grew" in text and "of the 2 stocks you picked" in text
    assert default_catalog().text("compare_note") in text
    assert_no_technical_text(text)


def test_chart_survives_theme_switch_and_a_phone(page: Page) -> None:
    show_compare(page)
    height = page.locator("#cmp-chart .main-svg").first.bounding_box()
    page.locator("#theme-toggle").click()
    page.wait_for_timeout(500)
    after = page.locator("#cmp-chart .main-svg").first.bounding_box()
    assert height is not None and after is not None and after["height"] >= height["height"] * 0.9
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert re.search(r"\d", count(page))
