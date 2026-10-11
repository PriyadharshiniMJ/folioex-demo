"""How the invested money is spread: by sector and by company size."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from folioex.analytics.holdings import CAP_ORDER, HoldingRow


@dataclass(frozen=True, slots=True)
class Slice:
    name: str
    value: float
    weight: float  # share of invested money
    gain_pct: float | None
    symbols: tuple[str, ...]


def _group(rows: Sequence[HoldingRow], key: Callable[[HoldingRow], str]) -> list[Slice]:
    invested = sum(r.value for r in rows)
    groups: dict[str, list[HoldingRow]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    slices = []
    for name, members in groups.items():
        value = sum(r.value for r in members)
        cost = sum(r.cost for r in members)
        slices.append(
            Slice(
                name=name,
                value=value,
                weight=value / invested if invested else 0.0,
                gain_pct=value / cost - 1 if cost else None,
                symbols=tuple(r.symbol for r in members),
            )
        )
    return slices


def by_sector(rows: Sequence[HoldingRow]) -> list[Slice]:
    return sorted(_group(rows, lambda r: r.sector), key=lambda s: -s.value)


def by_cap(rows: Sequence[HoldingRow]) -> list[Slice]:
    order = {name: i for i, name in enumerate(CAP_ORDER)}
    return sorted(_group(rows, lambda r: r.cap_bucket), key=lambda s: order.get(s.name, len(order)))
