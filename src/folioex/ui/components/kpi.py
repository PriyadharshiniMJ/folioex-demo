"""Summary cards: a label with a plain-English tooltip, a big number and a short note underneath."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from dash import html

from folioex.ui.components.common import info_tip

Tone = Literal["up", "down", "neutral"]


@dataclass(frozen=True, slots=True)
class Kpi:
    label: str
    value: str
    tip: str
    sub: str = ""
    tone: Tone = "neutral"
    hero: bool = False


def tone_of(value: float | None) -> Tone:
    if value is None or abs(value) < 0.005:
        return "neutral"
    return "up" if value > 0 else "down"


def arrow(value: float | None) -> str:
    """▲/▼ so gains and losses never rely on colour alone."""
    tone = tone_of(value)
    return "▲ " if tone == "up" else "▼ " if tone == "down" else ""


def kpi_card(k: Kpi) -> html.Div:
    return html.Div(
        [
            html.Div([k.label, info_tip(k.tip, k.label)], className="fx-kpi-label"),
            html.Div(k.value, className=f"fx-kpi-value fx-{k.tone}"),
            html.Div(k.sub, className="fx-kpi-sub") if k.sub else None,
        ],
        className="fx-kpi fx-hero" if k.hero else "fx-kpi",
    )


def render_kpis(kpis: Sequence[Kpi]) -> list[html.Div]:
    return [kpi_card(k) for k in kpis]
