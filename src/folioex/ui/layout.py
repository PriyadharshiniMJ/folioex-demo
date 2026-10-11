"""Page structure: top bar, four tabs, footer disclaimer, disclaimer modal, Explain side panel."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import dash_bootstrap_components as dbc
from dash import dcc, html

from folioex.config import Settings
from folioex.errors import MessageCatalog, default_catalog
from folioex.ui.components.common import aria
from folioex.ui.components.quiz import quiz_modal, stores
from folioex.ui.tabs import compare, data_hub, holdings, performance


@dataclass(frozen=True, slots=True)
class TabSpec:
    key: str
    label: str
    title: str
    subtitle: str


TABS: tuple[TabSpec, ...] = (
    TabSpec("datahub", "Data Hub", "Data Hub", "Bring in your transactions"),
    TabSpec("holdings", "Holdings", "Current Holdings", "What you own today"),
    TabSpec("performance", "Performance", "Historical Performance", "How you've done over time"),
    TabSpec("compare", "Compare", "Compare", "Your stocks vs your wishlist"),
)
TAB_NAMES = {t.key: t.title for t in TABS}


def page_titles(settings: Settings) -> dict[str, str]:
    """Browser tab titles, e.g. "Holdings · FolioEx"."""
    return {t.key: f"{t.label} · {settings.ui.app_name}" for t in TABS}


@lru_cache(maxsize=1)
def _explain_text() -> dict[str, Any]:
    return tomllib.loads(resources.files("folioex").joinpath("explain.toml").read_text(encoding="utf-8"))


def explain_content(tab: str, *, loaded: bool, catalog: MessageCatalog | None = None) -> html.Div:
    """Body of the Explain panel. Before data is loaded it explains the tab and how to start."""
    catalog = catalog or default_catalog()
    words = _explain_text()
    empty = words["empty"].get(tab, words["empty"]["unknown"])
    sections: list[Any] = [
        html.Div([html.H3("In a nutshell"), html.P(empty["nutshell"], className="fx-nutshell")]),
        html.Div([html.H3("How to start"), html.Ul([html.Li(step) for step in words["start_steps"]])]),
    ]
    return html.Div([*sections, html.P(catalog.text("explain_footer"), className="fx-explain-foot")])


def _top_bar(settings: Settings) -> html.Header:
    return html.Header(
        html.Div(
            [
                html.Div(
                    [
                        html.Img(src="/assets/folioex-logo.svg", alt="", className="fx-logo"),
                        html.Span(settings.ui.app_name, className="fx-name"),
                        html.Span(settings.ui.tagline, id="tagline", className="fx-tagline"),
                    ],
                    className="fx-brand",
                ),
                html.Div(
                    [
                        html.Button(
                            "◐",
                            id="theme-toggle",
                            className="fx-icon-btn",
                            title="Switch theme",
                            n_clicks=0,
                            **aria(label="Switch between light and dark theme"),
                        ),
                        html.Span("No files loaded", id="files-chip", className="fx-chip"),
                    ],
                    className="fx-top-controls",
                ),
            ],
            className="fx-topbar-inner",
        ),
        id="topbar",
        className="fx-topbar",
    )


def _panel(tab: TabSpec, settings: Settings, catalog: MessageCatalog) -> html.Div:
    if tab.key == "datahub":
        return data_hub.layout(settings, catalog)
    if tab.key == "holdings":
        return holdings.layout()
    if tab.key == "performance":
        return performance.layout(settings)
    return compare.layout(settings, catalog)


def _footer(catalog: MessageCatalog) -> html.Footer:
    return html.Footer(
        [
            html.Span("i", className="fx-info", **aria(hidden="true")),
            html.P(
                [
                    catalog.text("disclaimer_footer"),
                    " ",
                    html.Button(
                        "Read full disclaimer", id="disclaimer-open", className="fx-link", n_clicks=0
                    ),
                ]
            ),
        ],
        id="footer-disclaimer",
        className="fx-footer",
    )


def _disclaimer_modal(catalog: MessageCatalog) -> dbc.Modal:
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Disclaimer")),
            dbc.ModalBody(
                [
                    html.Div([html.Strong(title), html.P(body)], className="fx-disc-item")
                    for title, body in catalog.disclaimer()
                ]
            ),
            dbc.ModalFooter(
                dbc.Button("Close", id="disclaimer-close", className="fx-btn-primary", n_clicks=0)
            ),
        ],
        id="disclaimer-modal",
        is_open=False,
        centered=True,
        scrollable=True,
    )


def build_layout(settings: Settings, catalog: MessageCatalog | None = None) -> html.Div:
    catalog = catalog or default_catalog()
    return html.Div(
        [
            dcc.Store(id="theme-default", data=settings.ui.default_theme),
            dcc.Store(id="theme-store", storage_type="local"),
            dcc.Store(id="page-titles", data=page_titles(settings)),
            dcc.Store(id="title-sink"),
            dcc.Store(id="dataset-id", storage_type="memory"),
            dcc.Store(id="motion"),
            dcc.Store(id="benchmark-store", data=settings.benchmark.default),
            *stores(),
            _top_bar(settings),
            html.Main(
                dbc.Tabs(
                    [dbc.Tab(_panel(t, settings, catalog), label=t.label, tab_id=t.key) for t in TABS],
                    id="tabs",
                    active_tab="datahub",
                    className="fx-tabs",
                ),
                className="fx-main",
            ),
            _footer(catalog),
            _disclaimer_modal(catalog),
            quiz_modal(catalog),
            dbc.Offcanvas(
                html.Div(id="explain-body"),
                id="explain-panel",
                title="💡 Explain",
                placement="end",
                is_open=False,
                scrollable=True,
                className="fx-explain",
            ),
        ],
        className="fx-app",
    )
