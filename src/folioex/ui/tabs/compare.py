"""Compare tab: your holdings and stocks you're curious about, grown from $100 side by side."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update

from folioex.analytics import compare as cmp
from folioex.analytics.holdings import HoldingRow
from folioex.analytics.returns import PERIODS, Window, resolve_period
from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import Issue, IssueKind, MessageCatalog, Severity, UserMessage, default_catalog
from folioex.formatting import display_date, money, percent, plural
from folioex.ingestion.symbols import normalize_symbol
from folioex.market_data.base import Profile
from folioex.market_data.cache import CachedMarketData, MarketSnapshot
from folioex.market_data.universe import Universe
from folioex.ui import charts
from folioex.ui.components.common import aria, empty_state, info_tip, panel_header
from folioex.ui.components.data_table import Col, data_table
from folioex.ui.safety import friendly_alert, safe_callback
from folioex.ui.state import REGISTRY
from folioex.ui.tabs.holdings import GRAPH
from folioex.ui.tabs.performance import bench_name, period_text
from folioex.ui.theme import chart_colors

_TYPED = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_UP_DOWN = {"fx-up": "x > 0", "fx-down": "x < 0"}
_SEARCH_HELP = "Type a ticker or company name to search"


def _starred(column: str, rules: dict[str, str] | None = None) -> dict[str, str]:
    return {**(rules or {}), "fx-star": f"data.stars && data.stars.includes('{column}')"}


COLUMNS = [
    Col("ticker", "Ticker", min_width=90),
    Col("name", "Name", min_width=140),
    Col("type", "Type", min_width=135),
    Col(
        "ret",
        "Return",
        kind="pct_signed",
        min_width=100,
        tip="How much $100 grew over the period, dividends included.",
        class_rules=_starred("ret", _UP_DOWN),
    ),
    Col(
        "annual",
        "Yearly",
        kind="pct_signed",
        min_width=100,
        tip="The return as a per-year rate. Only shown for periods of a year or more.",
        class_rules=_starred("annual", _UP_DOWN),
    ),
    Col(
        "volatility",
        "Ups & downs",
        kind="pct",
        min_width=115,
        tip="How bumpy the ride was, per year. Lower is calmer.",
        class_rules=_starred("volatility"),
    ),
    Col(
        "drawdown",
        "Biggest drop",
        kind="pct",
        min_width=120,
        tip="The biggest fall from a high point during the period. Closer to 0% is better.",
        class_rules=_starred("drawdown"),
    ),
    Col(
        "sharpe",
        "Sharpe",
        kind="ratio",
        tip="Return for each unit of bumpiness, after what cash would have paid. Higher is better.",
        class_rules=_starred("sharpe"),
    ),
    Col(
        "dividend_yield",
        "Dividend yield",
        kind="pct",
        min_width=125,
        tip="Yearly dividends ÷ the current price, as reported by Yahoo Finance.",
        class_rules=_starred("dividend_yield"),
    ),
    Col("pe", "P/E", kind="ratio", tip="Price ÷ yearly earnings per share. Funds don't have one."),
    Col("cap", "Market cap", kind="compact", tip="What the whole company is worth on the stock market."),
]
_TYPE_WORDS = {
    "portfolio": "Whole portfolio",
    "holding": "Holding",
    "wishlist": "Wishlist",
    "benchmark": "Benchmark",
}


@dataclass(slots=True)
class CompareScreen:
    figure: go.Figure
    rows: list[dict[str, Any]]
    notes: list[Any]
    count_text: str
    period_note: str
    slots: dict[str, int]
    lines: list[cmp.Line]
    window: Window
    picks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Options:
    holdings: list[dict[str, Any]]
    wishlist: list[dict[str, Any]]
    note: str
    universe_note: str


# ---------- data ----------


def _fetch(
    data: Dataset, market: CachedMarketData, picks: list[str]
) -> tuple[pd.DataFrame, dict[str, Profile], MarketSnapshot | None]:
    """Total-return prices and company details for the picks: what the dataset has, plus what's missing."""
    adj = data.market.prices.adj_close
    need = [s for s in picks if s not in adj.columns or adj[s].dropna().empty]
    snap = None
    if need:
        first, last = data.daily.day(0), data.daily.day(len(data.daily.value) - 1)
        snap = market.load(need, first, last)
        adj = pd.concat([adj, snap.prices.adj_close], axis=1)
    profiles = {**(snap.profiles if snap else {}), **data.market.profiles}
    return adj, profiles, snap


def compare_screen(
    data: Dataset,
    market: CachedMarketData,
    settings: Settings,
    catalog: MessageCatalog,
    *,
    holdings: list[str],
    wishlist: list[str],
    period: str,
    benchmark: str,
    show_portfolio: bool,
    show_benchmark: bool,
    slots: dict[str, int],
    theme: str | None,
) -> CompareScreen:
    limit = settings.compare.max_tickers
    held, wish, _ = cmp.clamp_picks(holdings, wishlist, limit, newest="wishlist")
    picks = [*held, *wish]
    daily = data.daily
    window = resolve_period(period, daily.index)
    dates = daily.index[window.start_i : window.end_i + 1]
    rf = settings.analytics.risk_free_rate
    adj, profiles, snap = _fetch(data, market, picks)
    notes: list[Any] = []

    lines: list[cmp.Line] = []
    if show_portfolio:
        index = cmp.portfolio_index(daily.value, daily.flows, window.start_i, window.end_i)
        if (line := cmp.make_line("portfolio", "Whole portfolio", "portfolio", index, dates, rf)) is not None:
            lines.append(line)
    for kind, symbols in (("holding", held), ("wishlist", wish)):
        for symbol in symbols:
            if symbol not in adj.columns:
                continue
            profile = profiles.get(symbol)
            line = cmp.make_line(
                symbol,
                symbol,
                kind,  # type: ignore[arg-type]
                adj[symbol],
                dates,
                rf,
                dividend_yield=profile.dividend_yield if profile else None,
                pe=profile.pe if profile else None,
                market_cap=profile.market_cap if profile else None,
            )
            if line is not None:
                lines.append(line)
    name = bench_name(settings, benchmark)
    if show_benchmark and benchmark in data.market.prices.adj_close.columns:
        series = data.market.prices.adj_close[benchmark]
        if (line := cmp.make_line(benchmark, name, "benchmark", series, dates, rf)) is not None:
            lines.append(line)

    notes += _notes(catalog, settings, snap, picks, {line.key for line in lines}, lines)
    colors = chart_colors(theme)
    new_slots = cmp.assign_slots(slots, picks, limit)
    stars = cmp.best_keys(lines)
    rows = [
        _row(line, profiles, name, [c for c, keys in stars.items() if line.key in keys]) for line in lines
    ]
    count = catalog.text("compare_count").format(count=len(picks), limit=limit)
    return CompareScreen(
        figure=charts.growth_lines(lines, new_slots, colors),
        rows=rows,
        notes=notes,
        count_text=count,
        period_note=_period_note(window, settings.format.date_format),
        slots=new_slots,
        lines=lines,
        window=window,
        picks=picks,
    )


def _row(line: cmp.Line, profiles: dict[str, Profile], bench: str, stars: list[str]) -> dict[str, Any]:
    profile = profiles.get(line.key)
    if line.kind == "portfolio":
        ticker, label = "Portfolio", "Your account"
    elif line.kind == "benchmark":
        ticker, label = line.key, bench
    else:
        ticker, label = line.key, (profile.name if profile and profile.name else line.key)
    return {
        "key": line.key,
        "ticker": ticker,
        "name": label,
        "type": _TYPE_WORDS[line.kind],
        "ret": line.stats.ret,
        "annual": line.stats.annual,
        "volatility": line.stats.volatility,
        "drawdown": line.stats.drawdown,
        "sharpe": line.stats.sharpe,
        "dividend_yield": line.dividend_yield,
        "pe": line.pe,
        "cap": line.market_cap,
        "stars": stars,
    }


def _notes(
    catalog: MessageCatalog,
    settings: Settings,
    snap: MarketSnapshot | None,
    picks: list[str],
    drawn: set[str],
    lines: list[cmp.Line],
) -> list[Any]:
    issues: list[Issue] = []
    if snap is not None:
        if snap.status == "unavailable":
            issues.append(Issue(IssueKind.COMPARE_UNAVAILABLE, Severity.WARNING))
        else:
            issues += [i for i in snap.issues if i.kind is IssueKind.MARKET_DATA_OFFLINE]
            issues += [
                Issue(IssueKind.COMPARE_TICKER_NOT_FOUND, Severity.WARNING, {"symbol": s})
                for s in picks
                if s not in drawn and s in snap.missing
            ]
    issues += [
        Issue(
            IssueKind.COMPARE_STARTS_LATER,
            Severity.INFO,
            {"symbol": line.key, "date": display_date(line.since, settings.format.date_format)},
        )
        for line in lines
        if line.since is not None
    ]
    return [friendly_alert(catalog.render(i)) for i in issues]


def _period_note(window: Window, date_format: str) -> str:
    note = f"{display_date(window.start, date_format)} → {display_date(window.end, date_format)}"
    return note + (" · since your first transaction (all available)" if window.clamped else "")


# ---------- search boxes ----------


def _holding_label(r: HoldingRow) -> str:
    return f"{r.symbol} · {r.name}" if r.name and r.name != r.symbol else r.symbol


def _held_options(data: Dataset, chosen: Collection[str], limit: int) -> list[dict[str, Any]]:
    """Every current holding, biggest first; once ``limit`` picks are made the others can't be added."""
    full = len(set(chosen)) >= limit
    return [
        {"label": _holding_label(r), "value": r.symbol, "disabled": full and r.symbol not in chosen}
        for r in sorted(data.holdings.rows, key=lambda r: -r.value)
    ]


def option_lists(
    data: Dataset,
    universe: Universe,
    catalog: MessageCatalog,
    settings: Settings,
    *,
    holdings: list[str],
    wishlist: list[str],
    search: str | None,
    limit: int,
) -> Options:
    """What each box offers. At the limit nothing new can be chosen, and a note says why."""
    chosen = {*holdings, *wishlist}
    full = len(chosen) >= limit
    held_options = _held_options(data, chosen, limit)
    wish_options: list[dict[str, Any]] = []
    for symbol in wishlist:
        listing = universe.lookup(symbol)
        wish_options.append({"label": listing.label if listing else symbol, "value": symbol})
    universe_note = ""
    text = (search or "").strip()
    if text and not full:
        have = {o["value"] for o in wish_options}
        for listing in universe.search(text):
            if listing.symbol not in have and listing.symbol not in holdings:
                wish_options.append({"label": listing.label, "value": listing.symbol})
                have.add(listing.symbol)
        typed = text.upper()
        if _TYPED.match(typed):
            symbol = normalize_symbol(typed, settings.format.symbols, settings.format.null_token) or typed
            if symbol not in have and symbol not in holdings:
                wish_options.append({"label": f"Use {symbol} (typed)", "value": symbol})
        if not universe.available:
            universe_note = catalog.render(Issue(IssueKind.UNIVERSE_UNAVAILABLE, Severity.INFO)).text
    note = (
        catalog.render(Issue(IssueKind.COMPARE_LIMIT, Severity.INFO, {"limit": limit})).text if full else ""
    )
    return Options(held_options, wish_options, note, universe_note)


# ---------- Explain ----------


def explain(
    data: Dataset,
    market: CachedMarketData,
    catalog: MessageCatalog,
    settings: Settings,
    *,
    holdings: list[str],
    wishlist: list[str],
    period: str,
    benchmark: str,
    show_portfolio: bool,
    show_benchmark: bool,
) -> html.Div:
    s = compare_screen(
        data,
        market,
        settings,
        catalog,
        holdings=holdings,
        wishlist=wishlist,
        period=period,
        benchmark=benchmark,
        show_portfolio=show_portfolio,
        show_benchmark=show_benchmark,
        slots={},
        theme="light",
    )
    words = period_text(period, s.window, settings.format.date_format)
    portfolio = next((line for line in s.lines if line.kind == "portfolio"), None)
    picks = [line for line in s.lines if line.kind in ("holding", "wishlist")]
    sentences: list[str] = []
    if portfolio is not None:
        sentences.append(
            f"Over {words} your whole portfolio grew {percent(portfolio.stats.ret, decimals=1, sign=True)}."
        )
    if picks:
        if (beat := cmp.beat_count(s.lines)) is not None:
            sentences.append(f"{beat[0]} of the {plural(beat[1], 'stock')} you picked grew more.")
        best = max(picks, key=lambda line: line.stats.ret)
        sentences.append(
            f"{best.key} grew the most: $100 became {money(100 * (1 + best.stats.ret), decimals=0)}."
        )
        calm = max(picks, key=lambda line: line.stats.drawdown)
        sentences.append(
            f"{calm.key} had the smallest fall along the way ({percent(calm.stats.drawdown, decimals=1)})."
        )
    else:
        sentences.append(catalog.text("compare_hint").format(limit=settings.compare.max_tickers))
    portfolio_yours = (
        f"{percent(portfolio.stats.ret, decimals=1, sign=True)} over {words}." if portfolio else ""
    )
    sections = [
        (
            "Growth of $100",
            "Every line starts at $100 on the first day, so you can see which would have grown most. "
            "The thick line is your whole portfolio; solid lines are stocks you own, dashed lines are "
            "your wishlist.",
            portfolio_yours,
        ),
        (
            "Side by side",
            "The same stocks in numbers. A ★ marks the best in each column among your picks.",
            "",
        ),
    ]
    glossary = [
        ("Ups & downs (volatility)", "How much the price bounces around. Lower means a calmer ride."),
        ("Biggest drop", "The largest fall from a high point to a later low in this period."),
        ("Sharpe ratio", "Return for each unit of bumpiness, after what cash would have paid."),
        (
            "Whole portfolio line",
            "Your account's growth with the money you added taken out, so deposits aren't counted as growth.",
        ),
    ]
    body: list[Any] = [
        html.Div([html.H3("In a nutshell"), html.P(" ".join(sentences), className="fx-nutshell")]),
        html.Div(
            [
                html.H3("What each part shows"),
                html.Dl(
                    [
                        html.Div(
                            [
                                html.Dt(t),
                                html.Dd([d, html.Span(f" Yours: {y}", className="fx-yours") if y else None]),
                            ]
                        )
                        for t, d, y in sections
                    ]
                ),
            ]
        ),
        html.Div(
            [html.H3("Words to know"), html.Dl([html.Div([html.Dt(t), html.Dd(d)]) for t, d in glossary])]
        ),
        html.P(catalog.text("compare_note"), className="fx-explain-foot"),
        html.P(catalog.text("explain_footer"), className="fx-explain-foot"),
    ]
    return html.Div(body)


# ---------- layout ----------


def _field(label: str, control: Any, for_id: str) -> html.Div:
    return html.Div(
        [html.Label(label, htmlFor=for_id, className="fx-ctl-label"), control], className="fx-field"
    )


def layout(settings: Settings, catalog: MessageCatalog) -> html.Div:
    seg = {
        "inputClassName": "btn-check",
        "labelClassName": "fx-seg-btn",
        "labelCheckedClassName": "active",
        "className": "fx-seg",
        "inline": True,
    }
    limit = settings.compare.max_tickers
    boxes = html.Div(
        [
            _field(
                "My holdings",
                dcc.Dropdown(
                    id="cmp-holdings",
                    multi=True,
                    options=[],
                    value=[],
                    placeholder="Stocks you own…",
                    className="fx-dropdown",
                    labels={"no_options_found": "No more holdings to pick"},
                ),
                "cmp-holdings",
            ),
            _field(
                "Wishlist (NYSE & Nasdaq)",
                dcc.Dropdown(
                    id="cmp-wish",
                    multi=True,
                    options=[],
                    value=[],
                    placeholder="Search a ticker or company…",
                    className="fx-dropdown",
                    search_order="original",
                    labels={"no_options_found": _SEARCH_HELP},
                ),
                "cmp-wish",
            ),
        ],
        className="fx-grid-2",
    )
    controls = html.Div(
        [
            html.Span("Period", className="fx-muted"),
            dbc.RadioItems(
                id="cmp-period",
                options=[{"label": p, "value": p} for p in PERIODS],
                value=settings.compare.default_period,
                **seg,
            ),
            dbc.Switch(
                id="cmp-portfolio", label="Show my whole portfolio", value=settings.compare.show_portfolio
            ),
            html.Div(
                [
                    html.Label(
                        [
                            "Compare against ",
                            info_tip(
                                "A benchmark is a yardstick for the market, like the 500 largest U.S. firms.",
                                "benchmark compare",
                            ),
                        ],
                        htmlFor="cmp-bench",
                        className="fx-ctl-label",
                    ),
                    dbc.Select(
                        id="cmp-bench",
                        value=settings.benchmark.default,
                        className="fx-select",
                        options=[{"label": v, "value": k} for k, v in settings.benchmark.choices.items()],
                    ),
                    dbc.Switch(id="cmp-bench-show", label="Show it on the chart", value=False),
                ],
                className="fx-bench-ctl",
            ),
        ],
        className="fx-period-bar",
    )
    body = html.Div(
        [
            html.Div(
                [
                    boxes,
                    html.Div(
                        [
                            html.Span(
                                catalog.text("compare_hint").format(limit=limit), className="fx-caption-note"
                            ),
                            html.Strong(id="cmp-count", className="fx-count"),
                        ],
                        className="fx-card-head",
                    ),
                    html.Div(id="cmp-limit-note", **aria(live="polite")),
                    html.Div(id="cmp-universe-note"),
                    controls,
                ],
                className="fx-card fx-compare-controls",
            ),
            html.Div(id="cmp-notes"),
            html.Div(
                [
                    html.H2("Growth of $100"),
                    html.P(id="cmp-period-note", className="fx-caption-note"),
                    dcc.Loading(dcc.Graph(id="cmp-chart", config=GRAPH), type="dot"),
                ],
                className="fx-card",
            ),
            html.Div(
                [
                    html.H2("Side by side"),
                    data_table("cmp-grid", COLUMNS, [], row_id="key"),
                    html.P(
                        "★ = best in column among your picks · every header has an ⓘ in plain English",
                        className="fx-caption-note",
                    ),
                    html.P(catalog.text("compare_note"), className="fx-caption-note", id="cmp-disclaimer"),
                ],
                className="fx-card",
            ),
        ],
        id="compare-body",
        className="fx-panel",
        hidden=True,
    )
    header = panel_header("Compare", "Your stocks vs your wishlist", "compare")
    return html.Div(
        [
            header,
            empty_state("compare"),
            body,
            dcc.Store(id="cmp-slots", storage_type="memory"),
            dcc.Store(id="cmp-ready", storage_type="memory"),
        ],
        id="panel-compare",
        className="fx-panel",
    )


# ---------- callbacks ----------


def _keep(_message: UserMessage) -> Any:
    return no_update


def register(app: Dash, settings: Settings, market: CachedMarketData, universe: Universe) -> None:
    catalog = default_catalog()
    limit = settings.compare.max_tickers

    # New data resets the picks. "cmp-ready" changes in the same response as the picks, so the callbacks
    # below always see the new data and its starting picks together, never the new data with old picks.
    @app.callback(
        Output("cmp-holdings", "options"),
        Output("cmp-holdings", "value"),
        Output("cmp-wish", "value"),
        Output("cmp-ready", "data"),
        Input("dataset-id", "data"),
    )
    @safe_callback(lambda _m: (no_update,) * 4)
    def start_picks(dataset_id: str | None) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        if data is None:
            return [], [], [], None
        top = sorted(data.holdings.rows, key=lambda r: -r.value)[:2]
        return _held_options(data, [r.symbol for r in top], limit), [r.symbol for r in top], [], dataset_id

    @app.callback(
        Output("cmp-holdings", "options"),
        Output("cmp-wish", "options"),
        Output("cmp-limit-note", "children"),
        Output("cmp-universe-note", "children"),
        Input("cmp-ready", "data"),
        Input("cmp-holdings", "value"),
        Input("cmp-wish", "value"),
        Input("cmp-wish", "search_value"),
    )
    @safe_callback(lambda _m: (no_update,) * 4)
    def offer(
        dataset_id: str | None, holdings: list[str] | None, wishlist: list[str] | None, search: str | None
    ) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        if data is None:
            return [], [], "", ""
        o = option_lists(
            data,
            universe,
            catalog,
            settings,
            holdings=holdings or [],
            wishlist=wishlist or [],
            search=search,
            limit=limit,
        )
        full = friendly_alert(UserMessage(o.note, Severity.INFO)) if o.note else ""
        hint = friendly_alert(UserMessage(o.universe_note, Severity.INFO)) if o.universe_note else ""
        return o.holdings, o.wishlist, full, hint

    def fallback(message: UserMessage) -> tuple[Any, ...]:
        return (False, True, {}, [], "", [friendly_alert(message)], "", no_update)

    @app.callback(
        Output("compare-body", "hidden"),
        Output({"type": "empty-state", "tab": "compare"}, "hidden"),
        Output("cmp-chart", "figure"),
        Output("cmp-grid", "rowData"),
        Output("cmp-count", "children"),
        Output("cmp-notes", "children"),
        Output("cmp-period-note", "children"),
        Output("cmp-slots", "data"),
        Input("cmp-ready", "data"),
        Input("cmp-holdings", "value"),
        Input("cmp-wish", "value"),
        Input("cmp-period", "value"),
        Input("cmp-portfolio", "value"),
        Input("benchmark-store", "data"),
        Input("cmp-bench-show", "value"),
        Input("theme-store", "data"),
        State("cmp-slots", "data"),
        State("theme-default", "data"),
    )
    @safe_callback(fallback)
    def show(
        dataset_id: str | None,
        holdings: list[str] | None,
        wishlist: list[str] | None,
        period: str | None,
        show_portfolio: bool | None,
        benchmark: str | None,
        show_benchmark: bool | None,
        theme: str | None,
        slots: dict[str, int] | None,
        default_theme: str | None,
    ) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        bench = benchmark or settings.benchmark.default
        if data is None or data.daily.value.empty:
            return True, False, {}, [], "", [], "", {}
        s = compare_screen(
            data,
            market,
            settings,
            catalog,
            holdings=holdings or [],
            wishlist=wishlist or [],
            period=period or settings.compare.default_period,
            benchmark=bench,
            show_portfolio=bool(show_portfolio),
            show_benchmark=bool(show_benchmark),
            slots=slots or {},
            theme=theme or default_theme,
        )
        return False, True, s.figure, s.rows, s.count_text, s.notes, s.period_note, s.slots


__all__ = ["COLUMNS", "explain", "layout", "register"]
