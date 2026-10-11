"""Performance tab: how the account did over a period, against a benchmark, and what dividends paid."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate

from folioex.analytics.insights import performance_insights
from folioex.analytics.performance import PerformanceView, performance_view
from folioex.analytics.performers import Performer
from folioex.analytics.returns import PERIODS, Window
from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import Issue, IssueKind, MessageCatalog, Severity, UserMessage, default_catalog
from folioex.formatting import MONTHS, display_date, money, percent
from folioex.ui import charts
from folioex.ui.components.common import aria, empty_state, info_tip, panel_header
from folioex.ui.components.data_table import Col, base_options, data_table
from folioex.ui.components.kpi import Kpi, arrow, render_kpis, tone_of
from folioex.ui.safety import friendly_alert, safe_callback
from folioex.ui.state import REGISTRY
from folioex.ui.tabs.holdings import GRAPH, render_insights
from folioex.ui.theme import chart_colors

_PERIOD_WORDS = {
    "1M": "the last month",
    "3M": "the last 3 months",
    "6M": "the last 6 months",
    "YTD": "this year so far",
    "1Y": "the last year",
    "3Y": "the last 3 years",
    "All": "all your history",
}
_UP_DOWN = {"fx-up": "x > 0", "fx-down": "x < 0"}
DIV_YEAR_COLS = [
    Col("year", "Year", min_width=140),
    Col("gross", "Gross", kind="money2"),
    Col("tax", "Tax", kind="money2"),
    Col("fees", "Fees", kind="money2"),
    Col("net", "Net", kind="money2"),
    Col("yoy", "vs year before", kind="pct_signed", class_rules=_UP_DOWN),
]
DIV_STOCK_COLS = [
    Col("ticker", "Ticker"),
    Col("name", "Name", min_width=170),
    Col("payments", "Payments", kind="number"),
    Col("gross", "Gross", kind="money2"),
    Col("tax", "Tax", kind="money2"),
    Col("fees", "Fees", kind="money2"),
    Col("net", "Net", kind="money2"),
    Col(
        "yoc",
        "Yield on cost",
        kind="pct",
        tip="Dividends over the last 12 months ÷ what you paid for the shares.",
    ),
    Col("share", "Share of dividends", kind="pct"),
]


@dataclass(slots=True)
class PerformanceScreen:
    period_note: str
    kpis: list[Kpi]
    bench_title: str
    value: go.Figure
    bench: go.Figure
    best_card: html.Div
    worst_card: html.Div
    waterfall: go.Figure
    waterfall_caption: str
    heatmap: html.Table
    dividend_kpis: list[Kpi]
    div_month: go.Figure
    div_year: go.Figure
    top_payers: go.Figure
    div_year_rows: list[dict[str, Any]]
    div_stock_rows: list[dict[str, Any]]
    div_stock_total: dict[str, Any]
    realized: go.Figure
    contribution: go.Figure
    insights: list[tuple[str, str]]
    notes: list[Any]


# ---------- wording ----------


def bench_name(settings: Settings, code: str) -> str:
    """ "S&P 500 (SPY)" → "S&P 500"."""
    label = settings.benchmark.choices.get(code, code)
    return label.split(" (")[0]


def period_text(code: str, window: Window, date_format: str) -> str:
    if code == "Custom" and not window.clamped:
        return f"{display_date(window.start, date_format)} to {display_date(window.end, date_format)}"
    return "all your history" if window.clamped else _PERIOD_WORDS.get(code, "this period")


def _period_note(window: Window, date_format: str) -> str:
    note = f"{display_date(window.start, date_format)} → {display_date(window.end, date_format)}"
    return note + (" · since your first transaction (all available)" if window.clamped else "")


def _pts(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{'+' if value >= 0 else '−'}{abs(value) * 100:.1f} pts"


def kpis(view: PerformanceView, bench: str) -> list[Kpi]:
    return [
        Kpi(
            "Return",
            f"{arrow(view.twr)}{percent(view.twr, decimals=1, sign=True)}",
            "Time-weighted return: how well your investments did, ignoring when you added money. "
            "Used to compare with the market.",
            tone=tone_of(view.twr),
            hero=True,
        ),
        Kpi(
            "Yearly return",
            "—" if view.xirr is None else f"{arrow(view.xirr)}{percent(view.xirr, decimals=1, sign=True)}",
            "Money-weighted (XIRR): what your money actually earned per year, given when you added it.",
            sub="Shown for periods of 1 year or more" if view.xirr is None else "per year",
            tone=tone_of(view.xirr),
        ),
        Kpi(
            f"vs {bench}",
            f"{arrow(view.excess)}{_pts(view.excess)}",
            f"Your return minus the {bench}'s return over the same days. Positive means you did better.",
            sub=f"{bench}: {percent(view.bench_twr, decimals=1, sign=True)}"
            if view.bench_twr is not None
            else "Benchmark prices unavailable",
            tone=tone_of(view.excess),
        ),
        Kpi("Money added", money(view.added, decimals=0), "Deposits minus withdrawals in this period."),
        Kpi(
            "Dividends",
            money(view.income.net, decimals=0),
            "Dividends you kept after tax and fees in this period.",
            sub="net",
        ),
        Kpi(
            "Realized gains",
            f"{arrow(view.realized)}{money(view.realized, decimals=0, sign=True)}",
            "Profit or loss locked in by selling during this period.",
            tone=tone_of(view.realized),
        ),
    ]


def dividend_kpis(view: PerformanceView) -> list[Kpi]:
    gain = view.breakdown.end_value - view.breakdown.start_value - view.breakdown.added
    growth = view.dividend_growth
    return [
        Kpi(
            "Dividends this period",
            money(view.income.net, decimals=0),
            "Dividends kept after tax and fees.",
            sub="net, after tax and fees",
        ),
        Kpi(
            "Last 12 months",
            money(view.last12_net, decimals=0),
            "Dividends you kept over the most recent 12 months.",
        ),
        Kpi(
            "Share of your gain",
            percent(view.income.net / gain) if gain > 0 else "—",
            "How much of your gain in this period came from dividends.",
        ),
        Kpi(
            "Growth vs last year",
            percent(growth[1], decimals=0, sign=True) if growth else "—",
            "Net dividends in the last full year compared with the year before.",
            sub=f"{growth[0]} vs {growth[0] - 1}" if growth else "",
            tone=tone_of(growth[1] if growth else None),
        ),
    ]


# ---------- view model ----------


def year_rows(view: PerformanceView) -> list[dict[str, Any]]:
    """Dividends per year in the window. A year only partly inside it is labelled and never compared."""
    full_yoy = {y.year: y.yoy for y in view.all_years}
    window = view.window
    rows = []
    for y in view.income.by_year:
        covers_start = window.from_inception or window.start < date(y.year, 1, 1)
        full = covers_start and date(y.year, 12, 31) <= window.end
        label = str(y.year) if full else f"{y.year} ({'so far' if y.year == window.end.year else 'part'})"
        rows.append(
            {
                "year": label,
                "gross": y.gross,
                "tax": y.tax,
                "fees": y.fees,
                "net": y.net,
                "yoy": full_yoy.get(y.year) if full else None,
            }
        )
    return rows


def render_heatmap(view: PerformanceView, colors: dict[str, str]) -> html.Table:
    rets = {(m.year, m.month): m.ret for m in view.months}
    years = sorted({y for y, _ in rets})
    head = html.Thead(html.Tr([html.Th(""), *(html.Th(m) for m in MONTHS), html.Th("Year")]))
    rows = []
    for year in years:
        cells: list[Any] = [html.Th(str(year), scope="row")]
        growth = 1.0
        for month in range(1, 13):
            ret = rets.get((year, month))
            if ret is None:
                cells.append(html.Td("·", className="fx-blank"))
                continue
            growth *= 1 + ret
            cells.append(
                html.Td(
                    percent(ret, decimals=1, sign=True),
                    title=f"{MONTHS[month - 1]} {year}",
                    style={"background": charts.gain_color(ret / 0.06 * 0.5, colors)},
                )
            )
        cells.append(
            html.Td(
                percent(growth - 1, decimals=1, sign=True),
                className="fx-year",
                style={"background": charts.gain_color((growth - 1) / 0.3 * 0.5, colors)},
            )
        )
        rows.append(html.Tr(cells))
    return html.Table([head, html.Tbody(rows)], className="fx-heat")


def render_period_performers(
    title: str,
    *,
    label: str,
    performers: Sequence[Performer],
    view: PerformanceView,
    catalog: MessageCatalog,
    caption: str,
    colors: dict[str, str],
) -> html.Div:
    items = []
    for rank, p in enumerate(performers, start=1):
        tone = "fx-up" if p.ret > 0 else "fx-down" if p.ret < 0 else ""
        items.append(
            html.Li(
                [
                    html.Span(str(rank), className="fx-rank"),
                    html.Span(p.symbol, className="fx-tk"),
                    html.Span(
                        [p.name, html.Span("sold", className="fx-tag") if p.sold else None], className="fx-nm"
                    ),
                    html.Span(
                        f"{arrow(p.ret)}{percent(p.ret, decimals=1, sign=True)}", className=f"fx-pct {tone}"
                    ),
                    html.Span(money(p.gain, decimals=0, sign=True), className="fx-usd"),
                    html.Span(),
                ],
                className="fx-perf-row fx-static",
            )
        )
    body: Any = (
        html.Ul(items, className="fx-perf-list")
        if items
        else html.P("Not enough stocks to rank for this period.", className="fx-muted")
    )
    note = f"{caption} · {catalog.text('performers_caption')}"
    return html.Div(
        [
            html.H2([title, html.Span(f" ({label})", className="fx-muted fx-small")]),
            body,
            html.P(note, className="fx-caption-note"),
        ]
    )


def performance_screen(
    data: Dataset,
    settings: Settings,
    catalog: MessageCatalog,
    *,
    period: str,
    benchmark: str,
    custom: tuple[date, date] | None,
    running: bool,
    theme: str | None,
) -> PerformanceScreen:
    view = performance_view(data, settings, period=period, benchmark=benchmark, custom=custom)
    colors = chart_colors(theme)
    name = bench_name(settings, benchmark)
    fmt = settings.format.date_format
    words = period_text(period, view.window, fmt)
    b = view.breakdown
    inception = view.window.from_inception
    steps: list[tuple[str, float, str]] = []
    if not inception:
        steps.append(("Value at start", b.start_value, "absolute"))
    steps.append(
        ("Money put in" if inception else "Money added", b.added, "absolute" if inception else "relative")
    )
    steps += [
        ("Price gains", b.price_gains, "relative"),
        ("Dividends", b.dividends, "relative"),
        ("Tax withheld", b.withholding, "relative"),
        ("Fees", b.fees, "relative"),
    ]
    if abs(b.interest_other) >= 0.005:
        steps.append(("Interest & other", b.interest_other, "relative"))
    holdings_yoc = {r.symbol: r.yoc for r in data.holdings.rows}
    names = {r.symbol: r.name for r in data.holdings.rows} | {
        s: p.name for s, p in data.market.profiles.items() if p.name
    }
    stock_rows: list[dict[str, Any]] = [
        {
            "ticker": s.symbol,
            "name": names.get(s.symbol, s.symbol),
            "payments": s.payments,
            "gross": s.gross,
            "tax": s.tax,
            "fees": s.fees,
            "net": s.net,
            "yoc": holdings_yoc.get(s.symbol),
            "share": s.share,
        }
        for s in view.income.by_stock
    ]
    total = {
        "ticker": "Total",
        "name": "",
        "payments": sum(r["payments"] for r in stock_rows),
        "gross": view.income.gross,
        "tax": view.income.tax,
        "fees": view.income.fees,
        "net": view.income.net,
        "yoc": None,
        "share": 1.0 if stock_rows else None,
    }
    top = sorted(view.income.by_stock, key=lambda s: -s.net)[:8]
    years = year_rows(view)
    realized_years = [str(y) for y in view.realized_by_year]
    insights = [
        (i.severity, text)
        for i in performance_insights(view, name, words, settings.insights)
        if (text := catalog.insight(i))
    ]
    notes = [
        friendly_alert(
            catalog.render(Issue(IssueKind.SHADOW_CAPPED, Severity.INFO, {"date": display_date(d, fmt)}))
        )
        for d in (view.shadow.capped if view.shadow else [])
    ]
    return PerformanceScreen(
        period_note=_period_note(view.window, fmt),
        kpis=kpis(view, name),
        bench_title=f"You vs the {name}",
        value=charts.value_vs_deposits(view.value, view.deposits, colors),
        bench=charts.you_vs_benchmark(view.you, view.bench, name, colors),
        best_card=render_period_performers(
            "🏆 Best over this period",
            label=period if period != "Custom" else "custom",
            performers=view.best,
            view=view,
            catalog=catalog,
            caption="Includes dividends · ranked by % return",
            colors=colors,
        ),
        worst_card=render_period_performers(
            "🐢 Weakest over this period",
            label=period if period != "Custom" else "custom",
            performers=view.worst,
            view=view,
            catalog=catalog,
            caption="“sold” = no longer owned",
            colors=colors,
        ),
        waterfall=charts.gain_waterfall(steps, b.end_value, colors),
        waterfall_caption=(
            "From the money you put in to what your account is worth today."
            if inception
            else "From your account value at the start of the period to its value at the end."
        ),
        heatmap=render_heatmap(view, colors),
        dividend_kpis=dividend_kpis(view),
        div_month=charts.dividends_by_month(view.income.by_month, running, colors),
        div_year=charts.bars(
            [r["year"] for r in years],
            [r["net"] for r in years],
            [
                money(r["net"], decimals=0)
                + (f" ({percent(r['yoy'], sign=True)})" if r["yoy"] is not None else "")
                for r in years
            ],
            colors,
        ),
        top_payers=charts.bars(
            [s.symbol for s in top],
            [s.net for s in top],
            [money(s.net, decimals=0) for s in top],
            colors,
            horizontal=True,
        ),
        div_year_rows=years,
        div_stock_rows=stock_rows,
        div_stock_total=total,
        realized=charts.bars(
            realized_years,
            list(view.realized_by_year.values()),
            [money(v, decimals=0, sign=True) for v in view.realized_by_year.values()],
            colors,
            signed=True,
            height=260,
        ),
        contribution=charts.bars(
            [s for s, _ in view.contribution],
            [g for _, g in view.contribution],
            [money(g, decimals=0, sign=True) for _, g in view.contribution],
            colors,
            horizontal=True,
            signed=True,
            height=260,
        ),
        insights=[(str(sev), text) for sev, text in insights],
        notes=notes,
    )


def explain(
    data: Dataset,
    catalog: MessageCatalog,
    settings: Settings,
    *,
    period: str,
    benchmark: str,
    custom: tuple[date, date] | None,
) -> html.Div:
    s = performance_screen(
        data,
        settings,
        catalog,
        period=period,
        benchmark=benchmark,
        custom=custom,
        running=False,
        theme="light",
    )
    view = performance_view(data, settings, period=period, benchmark=benchmark, custom=custom)
    name = bench_name(settings, benchmark)
    words = period_text(period, view.window, settings.format.date_format)
    ret = percent(view.twr, decimals=1, sign=True)
    nutshell = f"Over {words} your investments returned {ret}"
    if view.bench_twr is not None:
        nutshell += (
            f", {'ahead of' if (view.excess or 0) >= 0 else 'behind'} the {name}'s "
            f"{percent(view.bench_twr, decimals=1, sign=True)}"
        )
    nutshell += f". Dividends paid you {money(view.income.net, decimals=0)} after tax and fees."
    best = view.best[0] if view.best else None
    sections = [
        (
            "Summary cards",
            "Your return for the period, your yearly return, and how you did against the market.",
            f"{ret} over {words}."
            + (
                f" {percent(view.xirr, decimals=1, sign=True)} a year on your money."
                if view.xirr is not None
                else " The yearly figure appears for periods of a year or more."
            ),
        ),
        (
            "Your account vs money you put in",
            "The shaded area is what your account was worth; the dotted line is "
            "what you had deposited. The gap is your gain.",
            f"{money(view.breakdown.end_value, decimals=0)} at the end.",
        ),
        (
            f"You vs the {name}",
            f"What would have happened if every deposit had gone into the {name} on the same day.",
            f"{_pts(view.excess)}." if view.excess is not None else "",
        ),
        (
            "Best and weakest over this period",
            "Ranked by % return in this period, including dividends and stocks you've since sold.",
            f"{best.name} was best ({percent(best.ret, decimals=1, sign=True)})." if best else "",
        ),
        (
            "Where your gains came from",
            "Bars add up from your starting money to your value now: price gains, "
            "dividends, minus tax and fees.",
            f"price gains of {money(view.breakdown.price_gains, decimals=0, sign=True)}.",
        ),
        ("Month by month", "Each cell is one month's return, so you can see good and bad stretches.", ""),
        (
            "Dividend income",
            "How much your stocks paid you, by month, by year and by stock.",
            f"{money(view.income.net, decimals=0)} kept in this period.",
        ),
        (
            "Gains from selling",
            "Profit or loss locked in by selling, and which stocks added most to your return.",
            f"{money(view.realized, decimals=0, sign=True)} realized in this period.",
        ),
    ]
    watch = [text for severity, text in s.insights if severity == "info"][:3]
    glossary = [
        (
            "Time-weighted return",
            "Your investments' growth, ignoring when you added money. Fair for comparing with the market.",
        ),
        ("Yearly return (XIRR)", "What your actual money earned per year, given when you deposited it."),
        ("Benchmark", f"A yardstick for the market. The {name} is the one you picked."),
        ("Tax withheld", "Tax taken from your dividends before they reach you."),
    ]
    body: list[Any] = [
        html.Div([html.H3("In a nutshell"), html.P(nutshell, className="fx-nutshell")]),
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


def _card(title: Any, *children: Any, caption: str | None = None, caption_id: str | None = None) -> html.Div:
    extra: dict[str, Any] = {"id": caption_id} if caption_id else {}
    cap = html.P(caption, className="fx-caption-note", **extra) if caption or caption_id else None
    head = html.H2(title) if isinstance(title, str) else title
    return html.Div([head, cap, *children], className="fx-card")


def _graph(graph_id: str) -> dcc.Graph:
    return dcc.Graph(id=graph_id, config=GRAPH)


def layout(settings: Settings) -> html.Div:
    seg = {
        "inputClassName": "btn-check",
        "labelClassName": "fx-seg-btn",
        "labelCheckedClassName": "active",
        "className": "fx-seg",
        "inline": True,
    }
    controls = html.Div(
        [
            html.Span("Period", className="fx-muted"),
            dbc.RadioItems(
                id="perf-period",
                options=[{"label": p, "value": p} for p in PERIODS]
                + [{"label": "📅 Custom", "value": "Custom"}],
                value="1Y",
                **seg,
            ),
            html.Div(
                dcc.DatePickerRange(id="perf-custom", display_format="DD/MM/YYYY"),
                id="perf-custom-wrap",
                hidden=True,
            ),
            html.Span(id="perf-period-note", className="fx-caption-note"),
            html.Div(
                [
                    html.Label(
                        [
                            "Compare against ",
                            info_tip(
                                "A benchmark is a yardstick for the market. FolioEx asks: what if every "
                                "dollar you deposited had gone into this index on the same day?",
                                "benchmark",
                            ),
                        ],
                        htmlFor="perf-bench",
                        className="fx-ctl-label",
                    ),
                    dbc.Select(
                        id="perf-bench",
                        value=settings.benchmark.default,
                        className="fx-select",
                        options=[{"label": v, "value": k} for k, v in settings.benchmark.choices.items()],
                    ),
                ],
                className="fx-bench-ctl",
            ),
        ],
        className="fx-period-bar",
    )
    body = html.Div(
        [
            controls,
            html.Div(id="perf-notes"),
            html.Div(id="perf-kpis", className="fx-kpis"),
            html.Div(
                [
                    _card(
                        "Your account vs money you put in",
                        _graph("perf-value"),
                        caption="The gap between the two is your gain.",
                    ),
                    _card(
                        html.H2("You vs the S&P 500", id="perf-bench-title"),
                        _graph("perf-bench-chart"),
                        caption="Same deposits, same days. Time-weighted, dividends included.",
                    ),
                ],
                className="fx-grid-2",
            ),
            html.Div(
                [
                    html.Div(id="perf-best", className="fx-card"),
                    html.Div(id="perf-worst", className="fx-card"),
                ],
                className="fx-grid-2",
            ),
            _card("Where your gains came from", _graph("perf-waterfall"), caption_id="perf-wf-caption"),
            _card(
                "Month by month",
                html.Div(id="perf-heatmap", className="fx-heat-wrap"),
                caption="Return each month. Green = up, red = down, with the number in every cell.",
            ),
            html.Div("Dividend income", className="fx-rule"),
            html.Div(id="perf-div-kpis", className="fx-kpis"),
            html.Div(
                [
                    _card(
                        html.Div(
                            [
                                html.H2("Dividends by month"),
                                dbc.RadioItems(
                                    id="perf-div-view",
                                    value="monthly",
                                    options=[
                                        {"label": "Monthly", "value": "monthly"},
                                        {"label": "Running total", "value": "running"},
                                    ],
                                    **seg,
                                ),
                            ],
                            className="fx-card-head",
                        ),
                        _graph("perf-div-month"),
                    ),
                    _card(
                        "Dividends by year",
                        _graph("perf-div-year"),
                        caption="Net dividends, with growth vs the year before.",
                    ),
                ],
                className="fx-grid-2",
            ),
            html.Div(
                [
                    _card(
                        "Top dividend payers",
                        _graph("perf-top-payers"),
                        caption="Net dividends in this period.",
                    ),
                    _card("Dividends by year", data_table("perf-div-year-grid", DIV_YEAR_COLS, [])),
                ],
                className="fx-grid-2",
            ),
            _card("Dividends by stock", data_table("perf-div-stock-grid", DIV_STOCK_COLS, [])),
            html.Div("Gains from selling", className="fx-rule"),
            html.Div(
                [
                    _card(
                        "Realized gains by year",
                        _graph("perf-realized"),
                        caption="Profit or loss locked in when you sold.",
                    ),
                    _card(
                        "Who added most to your return",
                        _graph("perf-contrib"),
                        caption="Dollar gain in this period, including dividends.",
                    ),
                ],
                className="fx-grid-2",
            ),
            _card("Insights", html.Div(id="perf-insights")),
        ],
        id="performance-body",
        className="fx-panel",
        hidden=True,
    )
    header = panel_header("Historical Performance", "How you've done over time", "performance")
    return html.Div([header, empty_state("performance"), body], id="panel-performance", className="fx-panel")


# ---------- callbacks ----------


def _fallback(message: UserMessage) -> tuple[Any, ...]:
    return (False, True, "", [friendly_alert(message)], *([no_update] * 20))


def _keep(_message: UserMessage) -> Any:
    return no_update


def _parse(day: str | None) -> date | None:
    return date.fromisoformat(day[:10]) if day else None


def register(app: Dash, settings: Settings) -> None:
    catalog = default_catalog()

    @app.callback(
        Output("perf-custom", "min_date_allowed"),
        Output("perf-custom", "max_date_allowed"),
        Output("perf-custom", "start_date"),
        Output("perf-custom", "end_date"),
        Input("dataset-id", "data"),
    )
    @safe_callback(lambda _m: (no_update,) * 4)
    def custom_bounds(dataset_id: str | None) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        if data is None or data.daily.value.empty:
            raise PreventUpdate
        first, last = data.daily.day(0), data.daily.day(len(data.daily.value) - 1)
        return first, last, date(last.year, 1, 1) if date(last.year, 1, 1) > first else first, last

    @app.callback(
        Output("performance-body", "hidden"),
        Output({"type": "empty-state", "tab": "performance"}, "hidden"),
        Output("perf-period-note", "children"),
        Output("perf-notes", "children"),
        Output("perf-kpis", "children"),
        Output("perf-value", "figure"),
        Output("perf-bench-title", "children"),
        Output("perf-bench-chart", "figure"),
        Output("perf-best", "children"),
        Output("perf-worst", "children"),
        Output("perf-waterfall", "figure"),
        Output("perf-wf-caption", "children"),
        Output("perf-heatmap", "children"),
        Output("perf-div-kpis", "children"),
        Output("perf-div-month", "figure"),
        Output("perf-div-year", "figure"),
        Output("perf-top-payers", "figure"),
        Output("perf-div-year-grid", "rowData"),
        Output("perf-div-stock-grid", "rowData"),
        Output("perf-div-stock-grid", "dashGridOptions"),
        Output("perf-realized", "figure"),
        Output("perf-contrib", "figure"),
        Output("perf-insights", "children"),
        Output("perf-custom-wrap", "hidden"),
        Input("dataset-id", "data"),
        Input("perf-period", "value"),
        Input("perf-custom", "start_date"),
        Input("perf-custom", "end_date"),
        Input("benchmark-store", "data"),
        Input("perf-div-view", "value"),
        Input("theme-store", "data"),
        State("theme-default", "data"),
    )
    @safe_callback(_fallback)
    def show(
        dataset_id: str | None,
        period: str | None,
        start: str | None,
        end: str | None,
        benchmark: str | None,
        div_view: str | None,
        theme: str | None,
        default_theme: str | None,
    ) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        period = period or "1Y"
        bench = benchmark or settings.benchmark.default
        if data is None or data.daily.value.empty:
            return (
                True,
                False,
                "",
                [],
                [],
                {},
                "",
                {},
                [],
                [],
                {},
                "",
                [],
                [],
                {},
                {},
                {},
                [],
                [],
                no_update,
                {},
                {},
                [],
                period != "Custom",
            )
        lo, hi = _parse(start), _parse(end)
        custom = (lo, hi) if period == "Custom" and lo and hi else None
        s = performance_screen(
            data,
            settings,
            catalog,
            period=period if custom or period != "Custom" else "1Y",
            benchmark=bench,
            custom=custom,
            running=div_view == "running",
            theme=theme or default_theme,
        )
        return (
            False,
            True,
            s.period_note,
            s.notes,
            render_kpis(s.kpis),
            s.value,
            s.bench_title,
            s.bench,
            s.best_card,
            s.worst_card,
            s.waterfall,
            s.waterfall_caption,
            s.heatmap,
            render_kpis(s.dividend_kpis),
            s.div_month,
            s.div_year,
            s.top_payers,
            s.div_year_rows,
            s.div_stock_rows,
            base_options() | {"pinnedBottomRowData": [s.div_stock_total]},
            s.realized,
            s.contribution,
            render_insights(s.insights),
            period != "Custom",
        )


__all__ = ["aria", "explain", "layout", "performance_screen", "register"]
