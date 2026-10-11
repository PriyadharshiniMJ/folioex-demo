"""Best and weakest stocks, ranked by % return. The two lists never share a stock."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from folioex.analytics.holdings import HoldingsView
from folioex.config import PerformersCfg


@dataclass(frozen=True, slots=True)
class Performer:
    symbol: str
    name: str
    ret: float
    gain: float
    start: date | None
    end: date | None
    sold: bool = False


def split_best_worst[T](
    items: Sequence[T], *, key: Callable[[T], float], count: int
) -> tuple[list[T], list[T]]:
    """Top ``count`` and bottom ``count`` (lowest first). With too few items, split them without repeats."""
    ranked = sorted(items, key=key, reverse=True)
    if len(ranked) < 2 * count:
        half = math.ceil(len(ranked) / 2)
        return ranked[:half], list(reversed(ranked[half:]))
    return ranked[:count], list(reversed(ranked[-count:]))


def since_purchase(
    view: HoldingsView, cfg: PerformersCfg, today: date
) -> tuple[list[Performer], list[Performer]]:
    """Current holdings by unrealized % gain since you bought them (brand-new or tiny positions left out)."""
    eligible = [
        Performer(r.symbol, r.name, r.gain_pct, r.gain, r.first_date, r.price_date)
        for r in view.rows
        if r.gain_pct is not None
        and r.cost >= cfg.min_avg_capital
        and (r.first_date is None or (today - r.first_date).days >= cfg.min_days)
    ]
    return split_best_worst(eligible, key=lambda p: p.ret, count=cfg.count)
