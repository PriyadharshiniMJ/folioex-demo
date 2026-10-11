"""Return-guess quiz: four ranges, exactly one right, wording that never contradicts the screen (A12)."""

import itertools
import random

import pytest

from folioex.analytics.quiz import Quiz, build_quiz, displayed, next_line, seed_for
from folioex.config import QuizCfg


def sweep() -> list[float]:
    """Every 0.05 percentage-point step from −95% to +1000%, as fractions."""
    return [i * 0.05 / 100 for i in range(-1900, 20001)]


def correct_options(quiz: Quiz, value: float) -> list[int]:
    shown = displayed(value)
    return [i for i, o in enumerate(quiz.options) if o.contains(shown)]


def test_exactly_one_range_is_right_for_every_value() -> None:
    for i, value in enumerate(sweep()):
        quiz = build_quiz(value, 10, seed=i)
        assert correct_options(quiz, value) == [quiz.correct], f"{value:.4%}"


def test_four_contiguous_ranges_with_whole_percent_boundaries() -> None:
    for value in (-0.95, -0.031, 0.0, 0.0496, 0.142, 0.605, 3.4, 10.0):
        for seed in range(8):
            quiz = build_quiz(value, 10, seed=seed)
            assert len(quiz.options) == 4
            assert quiz.options[0].low is None and quiz.options[-1].high is None
            for a, b in zip(quiz.options, quiz.options[1:], strict=False):
                assert a.high is not None and a.high == b.low  # no gap, no overlap
            bounds = [o.high for o in quiz.options[:-1]]
            assert all(b is not None and b == int(b) for b in bounds)
            widths = {b2 - b1 for b1, b2 in itertools.pairwise(bounds)}
            assert len(widths) == 1


def test_a_rounded_value_on_the_edge_goes_to_the_upper_range() -> None:
    # T-A12: 4.96% is shown as +5.0%, so "5% to 15%" is right and "Below 5%" is not.
    assert displayed(0.0496) == 5.0
    for seed in range(40):
        quiz = build_quiz(0.0496, 10, seed=seed)
        right = quiz.options[quiz.correct]
        assert right.contains(5.0)
        assert right.low in (None, 5)  # the edge belongs to the range that starts at it
        assert [o.label for o in quiz.options if o.contains(5.0)] == [right.label]
    mid = next(
        build_quiz(0.0496, 10, seed=s) for s in range(40) if build_quiz(0.0496, 10, seed=s).correct == 1
    )
    assert mid.options[1].label == "5% to 15%"


def test_the_edge_is_decided_by_the_shown_number_not_the_raw_one() -> None:
    assert displayed(0.04949) == 4.9
    assert displayed(0.0004) == 0.0
    assert displayed(-0.0004) == 0.0  # never "−0.0"
    quiz = build_quiz(-0.0004, 10, seed=1)
    assert correct_options(quiz, -0.0004) == [quiz.correct]


def test_labels_read_in_plain_english() -> None:
    quiz = build_quiz(0.142, 10, seed=0)
    labels = [o.label for o in quiz.options]
    assert len(set(labels)) == 4
    assert labels[0].startswith("Below ")
    assert labels[-1].endswith(" or more")
    assert all("None" not in label and "nan" not in label for label in labels)
    middle = [o.label for o in quiz.options[1:-1]]
    assert all(" to " in label for label in middle)


def test_negative_values_use_a_true_minus_sign() -> None:
    quiz = build_quiz(-0.12, 10, seed=2)
    labels = " ".join(o.label for o in quiz.options)
    assert "−" in labels and "-" not in labels
    assert correct_options(quiz, -0.12) == [quiz.correct]


def test_big_values_get_wider_ranges() -> None:
    small = build_quiz(0.3, 10, seed=1)
    big = build_quiz(9.5, 10, seed=1)  # +950%

    def width(q: Quiz) -> float:
        a, b = q.options[1].low, q.options[1].high
        assert a is not None and b is not None
        return b - a

    assert width(small) == 10
    assert width(big) > 10
    assert correct_options(big, 9.5) == [big.correct]


def test_a_fractional_setting_still_gives_whole_percent_boundaries() -> None:
    quiz = build_quiz(0.142, 2.5, seed=0)
    bounds = [o.high for o in quiz.options[:-1]]
    assert all(b is not None and b == int(b) for b in bounds)
    assert correct_options(quiz, 0.142) == [quiz.correct]


def test_same_seed_same_quiz_and_every_position_is_reachable() -> None:
    assert build_quiz(0.142, 10, seed=7) == build_quiz(0.142, 10, seed=7)
    positions = {build_quiz(0.142, 10, seed=s).correct for s in range(60)}
    assert positions == {0, 1, 2, 3}


def test_the_seed_comes_from_the_data() -> None:
    assert seed_for("a", 3, 10.5) == seed_for("a", 3, 10.5)
    assert seed_for("a", 3, 10.5) != seed_for("b", 3, 10.5)


def test_wrong_lines_never_repeat_back_to_back() -> None:
    lines = ["a", "b", "c", "d", "e"]
    rng = random.Random(11)
    last: int | None = None
    seen = set()
    for _ in range(2000):
        pick = next_line(lines, last, rng)
        assert pick != last
        seen.add(pick)
        last = pick
    assert seen == set(range(5))


def test_rotation_copes_with_few_lines() -> None:
    assert next_line(["only"], 0, random.Random(1)) == 0
    assert next_line([], None, random.Random(1)) == -1
    assert next_line(["a", "b"], 5, random.Random(1)) in (0, 1)  # a stale index is harmless


def test_quiz_settings() -> None:
    cfg = QuizCfg()
    assert cfg.enabled and cfg.bucket_width_pct == 10
    with pytest.raises(ValueError, match="greater than 0"):
        QuizCfg(bucket_width_pct=0)
