"""Holdings tab: what you own today, how it's spread, and what's driving gains and losses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from folioex.analytics.allocation import by_cap, by_sector
from folioex.analytics.holdings import HoldingRow, HoldingsView
from folioex.analytics.insights import data_quality_insight, holdings_insights
from folioex.analytics.performers import Performer, since_purchase
from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import IssueKind, MessageCatalog, UserMessage, default_catalog
from folioex.formatting import display_date, money, percent
from folioex.ui import charts
from folioex.ui.components.common import aria, empty_state, panel_header
from folioex.ui.components.data_table import Col, data_table
from folioex.ui.components.kpi import Kpi, arrow, render_kpis, tone_of
from folioex.ui.safety import friendly_alert, safe_callback
from folioex.ui.state import REGISTRY
from folioex.ui.theme import chart_colors

_UP_DOWN = {"fx-up": "x > 0", "fx-down": "x < 0"}
_ICONS = {"warning": ("⚠", "fx-sev-warning"), "positive": ("✓", "fx-sev-good"), "info": ("ℹ", "fx-sev-info")}
GRAPH: Any = {"displayModeBar": False, "responsive": True}

COLS = [
    Col("ticker", "Ticker", min_width=90),
    Col("name", "Name", min_width=180),
    Col("shares", "Shares", kind="number"),
    Col(
        "avg_cost",
        "Avg cost",
        kind="money2",
        tip="What you paid per share on average, including commissions.",
    ),
    Col("price", "Price", kind="money2", tip="Latest closing price."),
    Col("value", "Value", kind="money"),
    Col("weight", "Weight", kind="pct", tip="Share of your whole account, including cash."),
    Col("day_pct", "Day %", kind="pct_signed", class_rules=_UP_DOWN, tip="Change since the previous close."),
    Col("gain", "Gain $", kind="money_signed", class_rules=_UP_DOWN, tip="Unrealized gain since you bought."),
    Col("gain_pct", "Gain %", kind="pct_signed", class_rules=_UP_DOWN),
    Col(
        "dividends", "Dividends", kind="money", tip="Dividends from this stock after tax and fees, all time."
    ),
    Col(
        "yoc",
        "Yield on cost",
        kind="pct",
        tip="Dividends over the last 12 months ÷ what you paid. How much your original money earns.",
    ),
]


@dataclass(slots=True)
class HoldingsScreen:
    as_of_text: str
    market_note: list[Any]
    kpis: list[Kpi]
    insights: list[tuple[str, str]]
    best: list[Performer]
    worst: list[Performer]
    treemap: go.Figure
    sectors: go.Figure
    sizes: go.Figure
    drivers: go.Figure
    table_rows: list[dict[str, Any]]


# ---------- view model ----------


def kpis(view: HoldingsView) -> list[Kpi]:
    gain_pct = view.total_gain_pct
    base = view.invested - view.day_change
    return [
        Kpi(
            "Account value",
            money(view.account_value, decimals=0),
            "Everything in your account today: stocks, funds and cash.",
            hero=True,
        ),
        Kpi(
            "Total gain",
            f"{arrow(gain_pct)}{percent(gain_pct, decimals=1, sign=True)}",
            "(value today − money you put in) ÷ money you put in. How much your money has grown.",
            sub=(
                f"{money(view.total_gain, decimals=0, sign=True)} on "
                f"{money(view.net_deposits, decimals=0)} put in"
                if gain_pct is not None
                else "You've taken out more than you put in"
            ),
            tone=tone_of(gain_pct),
            hero=True,
        ),
        Kpi(
            "Cash",
            money(view.cash, decimals=0),
            "Money in your account that isn't invested.",
            sub=f"{percent(view.cash / view.account_value, decimals=1)} of account"
            if view.account_value
            else "",
        ),
        Kpi(
            "Invested",
            money(view.invested, decimals=0),
            "What everything you own is worth at the latest prices.",
        ),
        Kpi(
            "Unrealized gain",
            f"{arrow(view.unrealized)}{money(view.unrealized, decimals=0, sign=True)}",
            "Profit on paper: what you'd gain if you sold everything today.",
            tone=tone_of(view.unrealized),
        ),
        Kpi(
            "Today",
            f"{arrow(view.day_change)}{money(view.day_change, decimals=0, sign=True)}",
            "How much your holdings moved since the previous market close.",
            sub=percent(view.day_change / base, decimals=2, sign=True) if base > 0 else "",
            tone=tone_of(view.day_change),
        ),
        Kpi(
            "Realized gain",
            f"{arrow(view.realized)}{money(view.realized, decimals=0, sign=True)}",
            "Profit or loss you locked in by selling, all time.",
            tone=tone_of(view.realized),
        ),
        Kpi(
            "Dividends",
            money(view.dividends_net, decimals=0),
            "Dividends you kept after tax and fees, all time.",
            sub="net, all time",
        ),
    ]


def table_rows(rows: Sequence[HoldingRow]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": r.symbol,
            "name": r.name + (" · last trade price" if r.priced_from == "last trade" else ""),
            "shares": round(r.shares, 4),
            "avg_cost": r.avg_cost,
            "price": r.price,
            "value": r.value,
            "weight": r.weight,
            "day_pct": r.day_pct,
            "gain": r.gain,
            "gain_pct": r.gain_pct,
            "dividends": r.dividends_net,
            "yoc": r.yoc,
        }
        for r in rows
    ]


def holdings_screen(
    data: Dataset, settings: Settings, catalog: MessageCatalog, *, theme: str | None
) -> HoldingsScreen:
    view = data.holdings
    colors = chart_colors(theme)
    sectors = by_sector(view.rows)
    today = view.as_of or date.today()
    best, worst = since_purchase(view, settings.performers, today)
    notes = [*data.market.issues, *(i for i in view.issues if i.kind is IssueKind.TICKER_NOT_FOUND)]
    as_of = (
        f"as of {display_date(view.as_of, settings.format.date_format)}"
        if view.as_of
        else "prices unavailable · valued at last trade prices"
    )
    found = holdings_insights(view, sectors, settings.insights)
    if (quality := data_quality_insight(data.all_issues())) is not None:
        found = [quality, *found]  # a warning, so it leads
    return HoldingsScreen(
        as_of_text=as_of,
        market_note=[friendly_alert(catalog.render(i)) for i in notes],
        kpis=kpis(view),
        insights=[(i.severity, text) for i in found if (text := catalog.insight(i))],
        best=best,
        worst=worst,
        treemap=charts.treemap(view.rows, sectors, colors),
        sectors=charts.sector_donut(sectors, colors),
        sizes=charts.size_bars(by_cap(view.rows), colors),
        drivers=charts.gain_bars(view.rows, colors),
        table_rows=table_rows(view.rows),
    )


# ---------- rendering ----------


def render_insights(insights: Sequence[tuple[str, str]]) -> html.Ul:
    if not insights:
        return html.Ul(
            html.Li(
                [html.Span("✓", className="fx-ic"), html.Span("Nothing needs your attention.")],
                className="fx-issue fx-sev-good",
            ),
            className="fx-issues",
        )
    items = []
    for severity, text in insights:
        icon, css = _ICONS.get(severity, _ICONS["info"])
        items.append(
            html.Li(
                [html.Span(icon, className="fx-ic", **aria(hidden="true")), html.Span(text)],
                className=f"fx-issue {css}",
            )
        )
    return html.Ul(items, className="fx-issues")


def render_performers(
    title: str, performers: Sequence[Performer], data: Dataset, theme: str | None, catalog: MessageCatalog
) -> html.Div:
    colors = chart_colors(theme)
    items: list[Any] = []
    for rank, p in enumerate(performers, start=1):
        series = data.market.prices.series(p.symbol)
        if p.start is not None:
            series = series[series.index >= str(p.start)]
        line = charts.sparkline([float(v) for v in series], colors["up"] if p.ret >= 0 else colors["down"])
        tone = "fx-up" if p.ret > 0 else "fx-down" if p.ret < 0 else ""
        items.append(
            html.Li(
                html.Button(
                    [
                        html.Span(str(rank), className="fx-rank"),
                        html.Span(p.symbol, className="fx-tk"),
                        html.Span(p.name, className="fx-nm"),
                        html.Span(
                            f"{arrow(p.ret)}{percent(p.ret, decimals=1, sign=True)}",
                            className=f"fx-pct {tone}",
                        ),
                        html.Span(money(p.gain, decimals=0, sign=True), className="fx-usd"),
                        html.Img(src=line, alt="", className="fx-spark") if line else html.Span(),
                    ],
                    id={"type": "holding-jump", "symbol": p.symbol},
                    n_clicks=0,
                    className="fx-perf-row",
                    title=f"Show {p.symbol} in the holdings table",
                )
            )
        )
    body: Any = (
        html.Ul(items, className="fx-perf-list")
        if items
        else html.P("Not enough holdings to rank yet.", className="fx-muted")
    )
    caption = f"{catalog.text('performers_caption')} Click a row to find it in the table."
    return html.Div(
        [
            html.H2([title, html.Span(" (since you bought)", className="fx-muted fx-small")]),
            body,
            html.P(caption, className="fx-caption-note"),
        ]
    )


def explain(data: Dataset, catalog: MessageCatalog, settings: Settings) -> html.Div:
    view = data.holdings
    screen = holdings_screen(data, settings, catalog, theme="light")
    sectors = by_sector(view.rows)
    account = money(view.account_value, decimals=0)
    parts = [f"Your account is worth {account}."]
    if view.total_gain_pct is not None:
        parts.append(
            f"You've put in {money(view.net_deposits, decimals=0)}, so your money has grown "
            f"{percent(view.total_gain_pct, decimals=1, sign=True)}."
        )
    if sectors and view.rows:
        parts.append(
            f"{percent(sectors[0].weight)} of your investments are in {sectors[0].name.lower()}, "
            f"and {view.rows[0].name} is your biggest holding."
        )
    best = screen.best[0] if screen.best else None
    worst = screen.worst[0] if screen.worst else None
    biggest = max(view.rows, key=lambda r: r.gain, default=None)
    sections = [
        (
            "Summary cards",
            "Your account at a glance: what it's worth, your cash, and your gains.",
            f"{money(view.total_gain, decimals=0, sign=True)} overall, "
            f"{money(view.day_change, decimals=0, sign=True)} today.",
        ),
        (
            "Best and weakest right now",
            "Your strongest and weakest stocks since you bought them, by % gain.",
            f"{best.name} leads at {percent(best.ret, decimals=1, sign=True)}"
            + (f"; {worst.name} trails at {percent(worst.ret, decimals=1, sign=True)}." if worst else ".")
            if best
            else "",
        ),
        (
            "Where your money is",
            "Bigger box = more money in it. Green = up since you bought, red = down.",
            f"{sectors[0].name} is the biggest block." if sectors else "",
        ),
        ("By sector and company size", "How your money is spread across industries and company sizes.", ""),
        (
            "What's driving gains and losses",
            "Each bar is one stock's gain or loss in dollars since you bought.",
            f"{biggest.symbol} added the most ({money(biggest.gain, decimals=0, sign=True)})."
            if biggest
            else "",
        ),
        ("Your holdings", "One row per stock or fund you own. Click a column name to sort.", ""),
    ]
    watch = [text for severity, text in screen.insights if severity == "warning"][:3]
    glossary = [
        ("Unrealized gain", "Profit on paper: what you'd make if you sold today."),
        ("Realized gain", "Profit or loss you locked in when you sold."),
        ("Yield on cost", "Dividends over the last 12 months ÷ what you paid for the shares."),
        ("Weight", "How much of your account one stock makes up."),
    ]
    body: list[Any] = [
        html.Div([html.H3("In a nutshell"), html.P(" ".join(parts), className="fx-nutshell")]),
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
    ]
    if watch:
        body.append(html.Div([html.H3("Things to keep an eye on"), html.Ul([html.Li(w) for w in watch])]))
    body.append(
        html.Div(
            [html.H3("Words to know"), html.Dl([html.Div([html.Dt(t), html.Dd(d)]) for t, d in glossary])]
        )
    )
    body.append(html.P(catalog.text("explain_footer"), className="fx-explain-foot"))
    return html.Div(body)


# ---------- layout ----------


def _card(title: str, *children: Any, caption: str | None = None) -> html.Div:
    return html.Div(
        [html.H2(title), html.P(caption, className="fx-caption-note") if caption else None, *children],
        className="fx-card",
    )


def layout() -> html.Div:
    body = html.Div(
        [
            html.Div(id="holdings-market-note"),
            html.Div(id="holdings-kpis", className="fx-kpis"),
            _card("Insights", html.Div(id="holdings-insights")),
            html.Div(
                [
                    html.Div(id="holdings-best", className="fx-card"),
                    html.Div(id="holdings-worst", className="fx-card"),
                ],
                className="fx-grid-2",
            ),
            html.Div(
                [
                    _card(
                        "Where your money is",
                        dcc.Graph(id="holdings-treemap", config=GRAPH),
                        caption="Box size = money in it · colour = gain or loss since you bought "
                        "(also printed in each box)",
                    ),
                    html.Div(
                        [
                            _card(
                                "By sector",
                                dcc.Graph(id="holdings-sectors", config=GRAPH),
                            ),
                            _card(
                                "By company size",
                                dcc.Graph(id="holdings-sizes", config=GRAPH),
                            ),
                        ],
                        className="fx-stack",
                    ),
                ],
                className="fx-grid-main",
            ),
            _card(
                "What's driving your gains and losses",
                dcc.Graph(id="holdings-drivers", config=GRAPH),
                caption="Gain or loss since you bought, in dollars",
            ),
            _card(
                "Your holdings",
                data_table("holdings-grid", COLS, [], row_id="ticker", selectable=True),
                caption="Click a column name to sort.",
            ),
        ],
        id="holdings-body",
        className="fx-panel",
        hidden=True,
    )
    header = panel_header(
        "Current Holdings",
        "What you own today",
        "holdings",
        right=html.Span(id="holdings-asof", className="fx-muted fx-small"),
    )
    return html.Div([header, empty_state("holdings"), body], id="panel-holdings", className="fx-panel")


# ---------- callbacks ----------

_EMPTY_FIG: dict[str, Any] = {}


def _fallback(message: UserMessage) -> tuple[Any, ...]:
    return (False, True, "", [friendly_alert(message)], *([no_update] * 9))


def register(app: Dash, settings: Settings) -> None:
    catalog = default_catalog()

    @app.callback(
        Output("holdings-body", "hidden"),
        Output({"type": "empty-state", "tab": "holdings"}, "hidden"),
        Output("holdings-asof", "children"),
        Output("holdings-market-note", "children"),
        Output("holdings-kpis", "children"),
        Output("holdings-insights", "children"),
        Output("holdings-best", "children"),
        Output("holdings-worst", "children"),
        Output("holdings-treemap", "figure"),
        Output("holdings-sectors", "figure"),
        Output("holdings-sizes", "figure"),
        Output("holdings-drivers", "figure"),
        Output("holdings-grid", "rowData"),
        Input("dataset-id", "data"),
        Input("theme-store", "data"),
        State("theme-default", "data"),
    )
    @safe_callback(_fallback)
    def show(dataset_id: str | None, theme: str | None, default_theme: str | None) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        if data is None or not data.holdings.rows:
            return (True, False, "", [], [], [], [], [], _EMPTY_FIG, _EMPTY_FIG, _EMPTY_FIG, _EMPTY_FIG, [])
        theme = theme or default_theme
        s = holdings_screen(data, settings, catalog, theme=theme)
        return (
            False,
            True,
            s.as_of_text,
            s.market_note,
            render_kpis(s.kpis),
            render_insights(s.insights),
            render_performers("🏆 Best right now", s.best, data, theme, catalog),
            render_performers("🐢 Weakest right now", s.worst, data, theme, catalog),
            s.treemap,
            s.sectors,
            s.sizes,
            s.drivers,
            s.table_rows,
        )

    @app.callback(
        Output("holdings-grid", "scrollTo"),
        Output("holdings-grid", "selectedRows"),
        Input({"type": "holding-jump", "symbol": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(lambda _message: (no_update, no_update))
    def jump(clicks: list[int | None]) -> tuple[Any, Any]:
        if not any(clicks) or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        symbol = str(ctx.triggered_id["symbol"])
        return {"rowId": symbol, "rowPosition": "middle"}, {"ids": [symbol]}
