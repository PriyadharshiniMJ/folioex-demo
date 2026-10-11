"""Shared browser fixtures: a real FolioEx server on a free port, and a page that fails on console errors."""

import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from werkzeug.serving import make_server

from folioex.config import load_settings
from folioex.market_data.cache import CachedMarketData
from folioex.market_data.universe import Universe
from folioex.ui.app import create_app
from tests.fakes import FakeProvider, FakeUniverseSource

TABS = {"datahub": "Data Hub", "holdings": "Holdings", "performance": "Performance", "compare": "Compare"}


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    # A deterministic fake market keeps browser tests fast and independent of Yahoo Finance.
    app = create_app(
        load_settings(),
        market=CachedMarketData(FakeProvider(), None),
        universe=Universe(FakeUniverseSource(), None),
    )
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser, base_url: str) -> Iterator[Page]:
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on(
        "console", lambda m: errors.append(m.text) if m.type == "error" and "fonts.g" not in m.text else None
    )
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base_url)
    pg.wait_for_selector("#tabs")
    yield pg
    assert not errors, f"browser console errors (U7): {errors}"
    ctx.close()


def open_tab(page: Page, key: str) -> None:
    page.get_by_role("tab", name=TABS[key]).click()
    page.wait_for_timeout(150)


def header_alignments(page: Page, scope: str = "body") -> list[str]:
    """Computed alignment of every visible grid header label (criterion U2)."""
    return page.eval_on_selector_all(
        f"{scope} .ag-header-cell-label",
        "els => els.filter(e => e.offsetParent).map(e => getComputedStyle(e).justifyContent)",
    )


def skip_quiz(page: Page) -> None:
    """Dismiss the return-guess pop-up that follows every upload (it has its own tests)."""
    page.locator("#quiz-skip").click()
    page.wait_for_selector("#quiz-modal", state="hidden")


def load_sample(page: Page, *, quiz: bool = False) -> None:
    """Load the sample files from the Data Hub. The pop-up is dismissed unless the test is about it."""
    page.get_by_role("tab", name="Data Hub").click()
    page.locator("#load-sample").click()
    page.wait_for_selector("text=We loaded 6 files", timeout=60000)
    page.wait_for_selector("#quiz-modal .fx-quiz-option")
    if not quiz:
        skip_quiz(page)
