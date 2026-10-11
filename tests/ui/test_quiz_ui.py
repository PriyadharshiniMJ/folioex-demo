"""Return-guess pop-up: when it appears, what it asks and how it answers (criteria U5, E2, A12)."""

import random
from dataclasses import replace

import dash_bootstrap_components as dbc
import pytest

from folioex.analytics.quiz import displayed
from folioex.config import Settings, load_settings
from folioex.dataset import Dataset
from folioex.errors import MessageCatalog
from folioex.formatting import percent
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from folioex.ui.app import create_app
from folioex.ui.components import quiz as qz
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text, text_of, walk


@pytest.fixture(scope="module")
def data() -> Dataset:
    return process(
        sample_uploads(), load_settings(), NullProgress(), market=CachedMarketData(FakeProvider(), None)
    )


def test_quiz_is_built_from_the_total_gain_on_deposits(data: Dataset, settings: Settings) -> None:
    quiz = qz.quiz_for(data, settings)
    assert quiz is not None
    gain = data.holdings.total_gain_pct
    assert gain is not None
    assert quiz.shown == displayed(gain)
    assert quiz.options[quiz.correct].contains(quiz.shown)


def test_same_data_gives_the_same_quiz(data: Dataset, settings: Settings) -> None:
    assert qz.quiz_for(data, settings) == qz.quiz_for(data, settings)


def test_no_quiz_when_switched_off(data: Dataset, settings: Settings) -> None:
    off = settings.model_copy(update={"quiz": settings.quiz.model_copy(update={"enabled": False})})
    assert qz.quiz_for(data, off) is None


def test_no_quiz_without_a_percentage_to_guess(data: Dataset, settings: Settings) -> None:
    taken_out = replace(data, holdings=replace(data.holdings, total_gain_pct=None))
    assert qz.quiz_for(taken_out, settings) is None


def test_state_and_options_carry_four_choices(data: Dataset, settings: Settings) -> None:
    quiz = qz.quiz_for(data, settings)
    assert quiz is not None
    state = qz.quiz_state(quiz, data.holdings.total_gain_pct)
    options = qz.option_list(state)
    assert len(options) == 4
    assert [o["value"] for o in options] == [0, 1, 2, 3]
    assert len({o["label"] for o in options}) == 4
    assert state["gain"] == percent(data.holdings.total_gain_pct, decimals=1, sign=True)


def make_state(gain: float, correct: int = 1) -> dict[str, object]:
    return {
        "labels": ["Below 5%", "5% to 15%", "15% to 25%", "25% or more"],
        "correct": correct,
        "gain": percent(gain, decimals=1, sign=True),
        "up": gain >= 0,
    }


def test_right_guess_celebrates_and_names_the_gain(catalog: MessageCatalog) -> None:
    result = qz.result_view(make_state(0.142), 1, None, catalog, random.Random(1))
    assert result.right
    text = text_of(result.content)
    assert "Spot on" in text and "+14.2%" in text
    assert_no_technical_text(text)


def test_wrong_guess_gets_a_fun_line_and_the_real_number(catalog: MessageCatalog) -> None:
    result = qz.result_view(make_state(0.142), 3, None, catalog, random.Random(1))
    assert not result.right
    text = text_of(result.content)
    assert any(line in text for line in catalog.lines("quiz_wrong"))
    assert "+14.2%" in text and "Spot on" not in text
    assert result.line == catalog.lines("quiz_wrong").index(
        next(line for line in catalog.lines("quiz_wrong") if line in text)
    )


def test_fun_lines_rotate_and_never_repeat_back_to_back(catalog: MessageCatalog) -> None:
    rng = random.Random(5)
    last: int | None = None
    for _ in range(60):
        result = qz.result_view(make_state(0.142), 0, last, catalog, rng)
        assert result.line != last
        last = result.line


def test_a_loss_is_worded_as_a_loss(catalog: MessageCatalog) -> None:
    right = text_of(qz.result_view(make_state(-0.031, 1), 1, None, catalog, random.Random(1)).content)
    wrong = text_of(qz.result_view(make_state(-0.031, 1), 2, None, catalog, random.Random(1)).content)
    assert "−3.1%" in right and "grown" not in right
    assert "down" in wrong and "−3.1%" in wrong and "grew" not in wrong


def test_missing_wording_never_breaks_the_pop_up() -> None:
    result = qz.result_view(make_state(0.142), 0, None, MessageCatalog({}), random.Random(1))
    assert "+14.2%" in text_of(result.content)  # falls back to plain built-in wording
    assert_no_technical_text(text_of(result.content))


def test_modal_asks_the_question_and_offers_a_way_out(catalog: MessageCatalog) -> None:
    modal = qz.quiz_modal(catalog)
    assert isinstance(modal, dbc.Modal)
    assert modal.is_open is False
    text = text_of(modal)
    assert "Before you peek" in text and "money you put in" in text
    ids = {getattr(c, "id", None) for c in walk(modal)}
    assert {"quiz-choice", "quiz-lock", "quiz-skip", "quiz-done", "quiz-result"} <= ids
    assert_no_technical_text(text)


def test_app_registers_the_quiz(settings: Settings, catalog: MessageCatalog) -> None:
    app = create_app(settings, catalog, CachedMarketData(FakeProvider(), None))
    ids = {str(getattr(c, "id", None)) for c in walk(app.layout)}
    assert "quiz-modal" in ids
    assert any("quiz-modal" in str(k) for k in app.callback_map)
