"""Accessibility and wording across the whole app (criteria U6, U8, U9, U10)."""

import pytest
from playwright.sync_api import Page

from folioex.errors import default_catalog
from tests.e2e.conftest import load_sample, open_tab

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]

_NAMELESS = """
() => {
  const own = el => !el.closest('.ag-root-wrapper, .dash-dropdown-content, .modebar, .js-plotly-plot');
  const visible = el => el.offsetParent !== null;
  const named = el =>
    (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim();
  const buttons = [...document.querySelectorAll('button, [role=button]')].filter(e => own(e) && visible(e));
  const selects = [...document.querySelectorAll('select')].filter(visible);
  const bad = buttons.filter(e => !named(e)).map(e => 'button#' + e.id);
  for (const s of selects) {
    const label = document.querySelector('label[for="' + s.id + '"]');
    if (!s.getAttribute('aria-label') && !label) bad.push('select#' + s.id);
  }
  return bad;
}
"""


def show_every_tab(page: Page) -> None:
    load_sample(page)
    for tab in ("holdings", "performance", "compare"):
        open_tab(page, tab)
        page.wait_for_timeout(600)


def test_every_visible_button_and_select_has_a_name(page: Page) -> None:
    load_sample(page)
    for tab in ("datahub", "holdings", "performance", "compare"):
        open_tab(page, tab)
        page.wait_for_timeout(500)
        assert page.evaluate(_NAMELESS) == [], tab


def test_gains_and_losses_never_rely_on_colour_alone(page: Page) -> None:
    show_every_tab(page)
    for tab, panel in (("holdings", "#holdings-kpis"), ("performance", "#perf-kpis")):
        open_tab(page, tab)
        ups = page.locator(f"{panel} .fx-kpi-value.fx-up").all_inner_texts()
        downs = page.locator(f"{panel} .fx-kpi-value.fx-down").all_inner_texts()
        assert all(t.startswith("▲") for t in ups), (tab, ups)
        assert all(t.startswith("▼") for t in downs), (tab, downs)
        assert ups or downs, tab


def test_best_and_weakest_cards_follow_the_period_and_carry_the_caption(page: Page) -> None:
    load_sample(page)
    open_tab(page, "performance")
    page.wait_for_selector("#perf-best .fx-perf-row")
    caption = default_catalog().text("performers_caption")
    for card in ("#perf-best", "#perf-worst"):
        assert page.locator(f"{card} .fx-perf-row").count() <= 3
        assert caption in page.locator(card).inner_text()
    before = page.locator("#perf-best").inner_text() + page.locator("#perf-worst").inner_text()
    page.locator("#perf-period").get_by_text("3M", exact=True).click()
    page.wait_for_function(
        "(old) => { const q = s => document.querySelector(s).innerText;"
        " return q('#perf-best') + q('#perf-worst') !== old; }",
        arg=before,
    )


def test_the_disclaimer_is_on_every_tab_and_in_the_pop_up(page: Page) -> None:
    load_sample(page)
    footer = default_catalog().text("disclaimer_footer")
    for tab in ("datahub", "holdings", "performance", "compare"):
        open_tab(page, tab)
        assert footer in page.locator("#footer-disclaimer").inner_text()
    page.get_by_role("button", name="Read full disclaimer").click()
    modal = page.locator("#disclaimer-modal")
    modal.wait_for(state="visible")
    for title, _ in default_catalog().disclaimer():
        assert title in modal.inner_text()


def test_the_tagline_shows_next_to_the_name_on_a_desktop(page: Page) -> None:
    assert page.locator("#tagline").is_visible()
    assert page.locator("#tagline").inner_text() == "Your portfolio, explained."
