"""Return-guess pop-up in a real browser (criteria U5, A12, U7, E2)."""

import re

import pytest
from playwright.sync_api import Page

from folioex.errors import default_catalog
from tests.e2e.conftest import load_sample, open_tab, skip_quiz
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]


def options(page: Page) -> list[str]:
    return [t.strip() for t in page.locator("#quiz-modal .fx-quiz-option").all_inner_texts()]


def shown_gain(page: Page) -> float:
    """The growth percentage the Holdings tab displays, read after the pop-up is out of the way."""
    open_tab(page, "holdings")
    value = page.locator("#panel-holdings .fx-kpi", has_text="Total gain").locator(".fx-kpi-value").first
    match = re.search(r"([+−-]?)(\d[\d,]*\.\d)%", value.inner_text())
    assert match, value.inner_text()
    number = float(match.group(2).replace(",", ""))
    return -number if match.group(1) in ("−", "-") else number


def pick(page: Page, label: str) -> None:
    page.locator("#quiz-modal .fx-quiz-option", has_text=label).click()
    page.locator("#quiz-lock").click()
    page.wait_for_selector("#quiz-result-view", state="visible")


def right_label(page: Page, gain: float) -> str:
    for label in options(page):
        nums = [int(n.replace("−", "-")) for n in re.findall(r"[−-]?\d+", label)]
        if label.startswith("Below") and gain < nums[0]:
            return label
        if label.endswith("or more") and gain >= nums[0]:
            return label
        if " to " in label and nums[0] <= gain < nums[1]:
            return label
    raise AssertionError(f"no range holds {gain} in {options(page)}")


def test_pop_up_appears_after_loading_with_four_ranges(page: Page) -> None:
    load_sample(page, quiz=True)
    modal = page.locator("#quiz-modal")
    assert modal.is_visible()
    assert "Before you peek" in modal.inner_text()
    labels = options(page)
    assert len(labels) == 4 and len(set(labels)) == 4
    assert page.locator("#quiz-lock").is_disabled()  # nothing picked yet
    assert_no_technical_text(modal.inner_text())


def test_skip_closes_it_and_nothing_is_lost(page: Page) -> None:
    load_sample(page, quiz=True)
    skip_quiz(page)
    open_tab(page, "holdings")
    assert page.locator("#holdings-body").is_visible()


def test_right_guess_shows_confetti(page: Page) -> None:
    load_sample(page, quiz=True)
    labels = options(page)
    skip_quiz(page)
    gain = shown_gain(page)
    # a fresh load asks again; the range holding the shown gain is the right answer
    load_sample(page, quiz=True)
    assert options(page) == labels  # same data, same quiz
    pick(page, right_label(page, gain))
    text = page.locator("#quiz-result").inner_text()
    assert "Spot on" in text
    assert page.evaluate("document.body.getAttribute('data-confetti')") is not None
    assert page.locator("canvas[style*='fixed']").count() >= 1
    assert_no_technical_text(text)
    page.locator("#quiz-done").click()
    page.wait_for_selector("#quiz-modal", state="hidden")
    assert "active" in (page.get_by_role("tab", name="Holdings").get_attribute("class") or "")


def test_wrong_guess_gets_a_fun_line_and_the_real_number(page: Page) -> None:
    load_sample(page, quiz=True)
    labels = options(page)
    skip_quiz(page)
    gain = shown_gain(page)
    load_sample(page, quiz=True)
    wrong = next(label for label in labels if label != right_label(page, gain))
    pick(page, wrong)
    text = page.locator("#quiz-result").inner_text()
    assert any(line in text for line in default_catalog().lines("quiz_wrong"))
    assert re.search(r"[+−]\d[\d,]*\.\d%", text)
    assert "Spot on" not in text
    assert_no_technical_text(text)
    skip_quiz_done = page.locator("#quiz-done")
    skip_quiz_done.click()
    page.wait_for_selector("#quiz-modal", state="hidden")


def test_it_asks_again_after_every_upload_and_is_quiet_after_clearing(page: Page) -> None:
    load_sample(page, quiz=True)
    skip_quiz(page)
    load_sample(page, quiz=True)
    assert page.locator("#quiz-modal").is_visible()
    skip_quiz(page)
    page.get_by_role("tab", name="Data Hub").click()
    page.locator("#clear-data").click()
    page.wait_for_timeout(500)
    assert not page.locator("#quiz-modal").is_visible()


def test_pop_up_fits_a_phone(page: Page) -> None:
    page.set_viewport_size({"width": 375, "height": 700})
    load_sample(page, quiz=True)
    box = page.locator("#quiz-modal .modal-content").bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 375
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
