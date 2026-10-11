"""Shell callbacks: theme, browser title, Explain panel, disclaimer, empty-state navigation."""

from __future__ import annotations

from datetime import date
from typing import Any

from dash import ALL, Dash, Input, Output, State, ctx
from dash.exceptions import PreventUpdate

from folioex.config import Settings
from folioex.errors import UserMessage, default_catalog
from folioex.market_data.cache import CachedMarketData
from folioex.market_data.universe import Universe
from folioex.ui.components import quiz
from folioex.ui.layout import TAB_NAMES, explain_content
from folioex.ui.safety import friendly_alert, safe_callback
from folioex.ui.state import REGISTRY
from folioex.ui.tabs import compare, data_hub, holdings, performance

_THEME_JS = """
function(clicks, stored, fallback) {
    var current = stored || fallback || 'light';
    var next = clicks ? (current === 'dark' ? 'light' : 'dark') : current;
    var root = document.documentElement;
    root.setAttribute('data-theme', next);
    root.setAttribute('data-bs-theme', next);
    document.body.setAttribute('data-ag-theme-mode', next);
    return [next, next === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'];
}
"""

# One benchmark, two pickers (Performance and Compare). Both mirror the "benchmark-store" in the browser,
# so a choice shows up instantly on the other tab and a late server reply can never undo it.
_BENCH_PICK_JS = """
function(perf, cmp, current) {
    var from = window.dash_clientside.callback_context.triggered_id;
    var chosen = from === 'cmp-bench' ? cmp : perf;
    return (!chosen || chosen === current) ? window.dash_clientside.no_update : chosen;
}
"""
_BENCH_SHOW_JS = """
function(store, perf, cmp) {
    var keep = window.dash_clientside.no_update;
    return [store === perf ? keep : store, store === cmp ? keep : store];
}
"""

_TITLE_JS = """
function(tab, titles) {
    if (titles && titles[tab]) { document.title = titles[tab]; }
    return tab;
}
"""


def _explain_fallback(message: UserMessage) -> tuple[bool, Any, str]:
    return True, friendly_alert(message), "💡 Explain"


def _custom(period: str | None, start: str | None, end: str | None) -> tuple[date, date] | None:
    if period != "Custom" or not start or not end:
        return None
    return date.fromisoformat(start[:10]), date.fromisoformat(end[:10])


def _same(message: UserMessage) -> Any:
    raise PreventUpdate


def register_callbacks(app: Dash, settings: Settings, market: CachedMarketData, universe: Universe) -> None:
    data_hub.register(app, settings, market)
    holdings.register(app, settings)
    performance.register(app, settings)
    compare.register(app, settings, market, universe)
    quiz.register(app, settings)
    app.clientside_callback(
        _THEME_JS,
        Output("theme-store", "data"),
        Output("theme-toggle", "title"),
        Input("theme-toggle", "n_clicks"),
        State("theme-store", "data"),
        State("theme-default", "data"),
    )
    app.clientside_callback(
        "function(_) { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }",
        Output("motion", "data"),
        Input("theme-default", "data"),
    )
    app.clientside_callback(
        _BENCH_PICK_JS,
        Output("benchmark-store", "data"),
        Input("perf-bench", "value"),
        Input("cmp-bench", "value"),
        State("benchmark-store", "data"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        _BENCH_SHOW_JS,
        Output("perf-bench", "value"),
        Output("cmp-bench", "value"),
        Input("benchmark-store", "data"),
        State("perf-bench", "value"),
        State("cmp-bench", "value"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        _TITLE_JS,
        Output("title-sink", "data"),
        Input("tabs", "active_tab"),
        State("page-titles", "data"),
    )

    @app.callback(
        Output("explain-panel", "is_open"),
        Output("explain-body", "children"),
        Output("explain-panel", "title"),
        Input({"type": "explain-open", "tab": ALL}, "n_clicks"),
        State("dataset-id", "data"),
        State("perf-period", "value"),
        State("perf-custom", "start_date"),
        State("perf-custom", "end_date"),
        State("benchmark-store", "data"),
        State("cmp-holdings", "value"),
        State("cmp-wish", "value"),
        State("cmp-period", "value"),
        State("cmp-portfolio", "value"),
        State("cmp-bench-show", "value"),
        prevent_initial_call=True,
    )
    @safe_callback(_explain_fallback)
    def open_explain(
        clicks: list[int | None],
        dataset_id: str | None,
        period: str | None,
        start: str | None,
        end: str | None,
        benchmark: str | None,
        cmp_held: list[str] | None,
        cmp_wish: list[str] | None,
        cmp_period: str | None,
        cmp_portfolio: bool | None,
        cmp_bench: bool | None,
    ) -> tuple[bool, Any, str]:
        if not any(clicks) or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        tab = str(ctx.triggered_id["tab"])
        title = f"💡 Explaining: {TAB_NAMES.get(tab, 'FolioEx')}"
        dataset = REGISTRY.get(dataset_id)
        if dataset is not None and tab == "datahub":
            return (
                True,
                data_hub.explain(dataset.ingest, default_catalog(), settings.format.date_format),
                title,
            )
        if dataset is not None and tab == "holdings":
            return True, holdings.explain(dataset, default_catalog(), settings), title
        if dataset is not None and tab == "performance":
            content = performance.explain(
                dataset,
                default_catalog(),
                settings,
                period=period or "1Y",
                benchmark=benchmark or settings.benchmark.default,
                custom=_custom(period, start, end),
            )
            return True, content, title
        if dataset is not None and tab == "compare":
            content = compare.explain(
                dataset,
                market,
                default_catalog(),
                settings,
                holdings=cmp_held or [],
                wishlist=cmp_wish or [],
                period=cmp_period or settings.compare.default_period,
                benchmark=benchmark or settings.benchmark.default,
                show_portfolio=bool(cmp_portfolio),
                show_benchmark=bool(cmp_bench),
            )
            return True, content, title
        return True, explain_content(tab, loaded=dataset is not None), title

    @app.callback(
        Output("disclaimer-modal", "is_open"),
        Input("disclaimer-open", "n_clicks"),
        Input("disclaimer-close", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_same)
    def toggle_disclaimer(_open: int | None, _close: int | None) -> bool:
        return bool(ctx.triggered_id == "disclaimer-open")

    @app.callback(
        Output("tabs", "active_tab"),
        Input({"type": "goto", "from": ALL, "to": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_same)
    def go_to_tab(clicks: list[int | None]) -> str:
        if not any(clicks) or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        return str(ctx.triggered_id["to"])
