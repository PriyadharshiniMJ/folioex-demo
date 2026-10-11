"""Small shared building blocks: info tooltips, panel headers, explain button, empty states."""

from __future__ import annotations

import itertools
import re
from typing import Any

import dash_bootstrap_components as dbc
from dash import html

_tip_ids = itertools.count()


def aria(**attrs: str) -> dict[str, Any]:
    """ARIA attributes for Dash components, e.g. ``aria(hidden="true")`` -> ``{"aria-hidden": "true"}``."""
    return {f"aria-{key}": value for key, value in attrs.items()}


def info_tip(text: str, name: str) -> html.Span:
    """An ⓘ marker with a plain-English tooltip, reachable by keyboard."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    target = f"tip-{slug}-{next(_tip_ids)}"
    return html.Span(
        [
            html.Span("i", id=target, className="fx-info", tabIndex="0", role="img", **aria(label=text)),
            dbc.Tooltip(text, target=target, placement="top"),
        ]
    )


def explain_button(tab: str) -> dbc.Button:
    return dbc.Button(
        "💡 Explain",
        id={"type": "explain-open", "tab": tab},
        className="fx-explain-btn",
        n_clicks=0,
    )


def panel_header(title: str, subtitle: str, tab: str, right: Any | None = None) -> html.Div:
    return html.Div(
        [
            html.Div([html.H1(title), html.P(subtitle, className="fx-sub")]),
            html.Div(
                [right, explain_button(tab)] if right is not None else [explain_button(tab)],
                className="fx-right",
            ),
        ],
        className="fx-panel-head",
    )


def empty_state(tab: str) -> html.Div:
    return html.Div(
        [
            html.Div("📂", className="fx-empty-icon", **aria(hidden="true")),
            html.P("Nothing here yet", className="fx-lead"),
            html.P(
                "Upload your transaction history to see what you own and how it's doing.",
                className="fx-muted",
            ),
            html.Div(
                [
                    dbc.Button(
                        "Go to Data Hub",
                        id={"type": "goto", "from": tab, "to": "datahub"},
                        className="fx-btn",
                        n_clicks=0,
                    ),
                    dbc.Button(
                        "▶ Load sample data",
                        id={"type": "load-sample", "from": tab},
                        className="fx-btn-primary",
                        n_clicks=0,
                    ),
                ],
                className="fx-btn-row",
            ),
        ],
        id={"type": "empty-state", "tab": tab},
        className="fx-card fx-empty",
    )
