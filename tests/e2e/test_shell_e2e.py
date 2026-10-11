"""Browser smoke tests for the app shell (criteria U1, U3, U4, U7, U9, U10). Run with: pytest -m e2e"""

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import TABS, open_tab
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]


def test_tab_titles_follow_the_section(page: Page) -> None:
    for key, label in TABS.items():
        open_tab(page, key)
        page.wait_for_function(f"document.title === '{label} · FolioEx'")


def test_explain_panel_on_every_tab(page: Page) -> None:
    for key in TABS:
        open_tab(page, key)
        page.locator(f"#panel-{key} .fx-explain-btn").click()
        panel = page.locator("#explain-panel")
        panel.wait_for(state="visible")
        text = panel.inner_text()
        assert "in a nutshell" in text.lower()  # headings are uppercased by CSS
        assert "isn't financial advice" in text
        assert_no_technical_text(text)
        page.keyboard.press("Escape")
        panel.wait_for(state="hidden")


def test_top_bar_footer_and_disclaimer(page: Page) -> None:
    assert page.locator("#tagline").inner_text() == "Your portfolio, explained."
    assert page.locator("#topbar select").count() == 0
    assert "not investment" in page.locator("#footer-disclaimer").inner_text()
    page.locator("#disclaimer-open").click()
    modal = page.locator("#disclaimer-modal")
    modal.wait_for(state="visible")
    assert "Not investment advice" in modal.inner_text()
    page.locator("#disclaimer-close").click()
    modal.wait_for(state="hidden")


def test_theme_toggle_switches_colours(page: Page) -> None:
    before = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.locator("#theme-toggle").click()
    page.wait_for_function("document.documentElement.getAttribute('data-theme') === 'dark'")
    after = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert before != after
    page.locator("#theme-toggle").click()
    page.wait_for_function("document.documentElement.getAttribute('data-theme') === 'light'")


def test_empty_state_leads_back_to_data_hub(page: Page) -> None:
    open_tab(page, "performance")
    page.locator("#panel-performance").get_by_role("button", name="Go to Data Hub").click()
    page.wait_for_function("document.title === 'Data Hub · FolioEx'")
    assert page.locator("#panel-datahub").is_visible()


def test_phone_width_has_no_sideways_scroll(page: Page) -> None:
    page.set_viewport_size({"width": 375, "height": 800})
    for key in TABS:
        open_tab(page, key)
        scroll, client = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        assert scroll <= client, f"{key}: page scrolls sideways ({scroll} > {client})"
    assert not page.locator("#tagline").is_visible()


def test_no_technical_text_anywhere(page: Page) -> None:
    for key in TABS:
        open_tab(page, key)
        assert_no_technical_text(page.locator("body").inner_text())
