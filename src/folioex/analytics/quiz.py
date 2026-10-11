"""The return-guess quiz: four ranges around the real total gain, exactly one of them right.

Ranges are half-open [low, high) and are decided on the number the investor will actually see (rounded to
one decimal), so a gain shown as +5.0% is never marked wrong for the "5% to 15%" guess. The first and last
ranges are open-ended, so every possible gain has exactly one home.
"""

from __future__ import annotations

import itertools
import math
import random
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

_OPTIONS = 4
_WIDEN_AT = 10  # ranges widen once the gain is more than this many range-widths away from zero


@dataclass(frozen=True, slots=True)
class Option:
    low: int | None  # None = no lower limit
    high: int | None  # None = no upper limit
    label: str

    def contains(self, shown: float) -> bool:
        return (self.low is None or shown >= self.low) and (self.high is None or shown < self.high)


@dataclass(frozen=True, slots=True)
class Quiz:
    options: tuple[Option, ...]
    correct: int
    shown: float  # the gain as displayed, in percent


def displayed(gain: float) -> float:
    """The gain in percent points as it is shown on screen (one decimal, never a negative zero)."""
    return round(gain * 100, 1) + 0.0


def seed_for(*parts: object) -> int:
    """A stable seed from the data, so the right answer's position doesn't change on every refresh."""
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))


def _percent(bound: int) -> str:
    return f"{'−' if bound < 0 else ''}{abs(bound)}%"


def _width(shown: float, base: float) -> int:
    widen = max(1, math.ceil(abs(shown) / (_WIDEN_AT * base)))
    return max(1, round(base * widen))


def build_quiz(gain: float, base_width_pct: float, *, seed: int) -> Quiz:
    """Four contiguous ranges; the one holding ``gain`` sits in a seeded position."""
    shown = displayed(gain)
    width = _width(shown, base_width_pct)
    half = width // 2
    start = math.floor((shown - half) / width) * width + half  # lower edge of the range holding the gain
    correct = random.Random(seed).randrange(_OPTIONS)
    bounds = [start + (k - correct + 1) * width for k in range(_OPTIONS - 1)]
    options = [Option(None, bounds[0], f"Below {_percent(bounds[0])}")]
    options += [Option(lo, hi, f"{_percent(lo)} to {_percent(hi)}") for lo, hi in itertools.pairwise(bounds)]
    options.append(Option(bounds[-1], None, f"{_percent(bounds[-1])} or more"))
    return Quiz(tuple(options), correct, shown)


def next_line(lines: Sequence[str], last: int | None, rng: random.Random) -> int:
    """Index of the next fun line: random, never the one shown last. -1 when there are none."""
    if not lines:
        return -1
    fresh = [i for i in range(len(lines)) if i != last]
    return rng.choice(fresh) if fresh else 0
