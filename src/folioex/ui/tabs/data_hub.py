"""Data Hub: template download, uploads, per-file report, coverage, things to know, transaction preview."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.dcc.express import send_string
from dash.exceptions import PreventUpdate

from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.errors import Issue, IssueKind, MessageCatalog, Severity, UserMessage, default_catalog
from folioex.formatting import MONTHS, display_date, money, period_label, plural
from folioex.ingestion.pipeline import FileReport, IngestResult, Upload
from folioex.ingestion.template import column_guide, template_csv
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from folioex.ui.components.candle_progress import progress_view
from folioex.ui.components.common import aria, panel_header
from folioex.ui.components.data_table import Col, data_table
from folioex.ui.safety import friendly_alert, safe_callback
from folioex.ui.state import JOBS, REGISTRY

TEMPLATE_FILENAME = "FolioEx_template.csv"
_SEVERITY_RANK = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
ALL_TYPES = "__all__"
_ICON = {Severity.BLOCKING: "✗", Severity.WARNING: "⚠", Severity.INFO: "ℹ"}
_STATUS = {"ok": "✓ Loaded", "warn": "⚠ Loaded with notes", "bad": "✗ Not loaded"}
_TONE_RULES = {
    "fx-ok": "x && x.startsWith('✓')",
    "fx-warn": "x && x.startsWith('⚠')",
    "fx-bad": "x && x.startsWith('✗')",
}

FILE_COLS = [
    Col("file", "File", min_width=180),
    Col("period", "Period"),
    Col("rows", "Rows", kind="number", tip="Transactions we read from this file."),
    Col("skipped", "Skipped", kind="number", tip="Rows we couldn't use. See “Things to know” for why."),
    Col(
        "cash",
        "Cash check",
        tip="Each file states its starting and ending cash. We check that every transaction adds up to it.",
        class_rules=_TONE_RULES,
    ),
    Col("status", "Status", class_rules=_TONE_RULES, min_width=170),
]
TX_COLS = [
    Col("date", "Date", kind="date", min_width=118),
    Col("type", "Type", min_width=150),
    Col("symbol", "Symbol"),
    Col("quantity", "Quantity", kind="number", tip="Change in shares: + bought or received, − sold or sent."),
    Col("price", "Price", kind="money2"),
    Col("gross", "Gross", kind="money2"),
    Col("commission", "Commission", kind="money2"),
    Col("net", "Net", kind="money2", tip="What actually moved in or out of your cash."),
    Col("file", "File", min_width=150),
]
GUIDE_COLS = [
    Col("column", "Column"),
    Col("data_type", "Data type"),
    Col("format", "Format", min_width=260, wrap=True),
    Col("placeholder", "Placeholder"),
    Col("required", "Required"),
]


# ---------- pure helpers (unit-tested) ----------


def decode_uploads(contents: list[str], names: list[str]) -> list[Upload]:
    """dcc.Upload gives data URLs. A malformed one becomes empty bytes, which is reported as unreadable."""
    uploads: list[Upload] = []
    for content, name in zip(contents, names, strict=False):
        try:
            _, payload = content.split(",", 1)
            data = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error):
            data = b""
        uploads.append(Upload(name, data))
    return uploads


def process_uploads(
    contents: list[str], names: list[str], settings: Settings, market: CachedMarketData
) -> tuple[str | None, html.Div]:
    """Synchronous path (tests, scripts): read the files, work out the numbers and present the outcome."""
    return finish_processing(
        process(decode_uploads(contents, names), settings, NullProgress(), market=market)
    )


def finish_processing(data: Dataset) -> tuple[str | None, html.Div]:
    """Store a finished dataset for this session and say, in plain English, what happened."""
    result = data.ingest
    catalog = default_catalog()
    problems = [f for f in result.files if f.status == "bad"]
    alerts = [friendly_alert(catalog.render(i)) for f in problems for i in f.issues]
    alerts += [friendly_alert(catalog.render(i)) for i in result.issues if i.kind.value == "too_many_files"]
    if not result.transactions:
        if not problems:
            alerts.append(
                friendly_alert(
                    UserMessage(
                        "We read your file, but it doesn't have any transactions yet. "
                        "Replace the template placeholders with your own data and upload it again.",
                        Severity.INFO,
                    )
                )
            )
        return None, html.Div(alerts)
    loaded = result.loaded_files
    summary = UserMessage(
        f"We loaded {plural(len(loaded), 'file')} with {plural(len(result.transactions), 'transaction')}."
        " Your report is below.",
        Severity.INFO,
    )
    market = [friendly_alert(catalog.render(i)) for i in data.market.issues]
    return REGISTRY.put(data), html.Div([friendly_alert(summary), *market, *alerts])


def _cash_label(report: FileReport) -> str:
    if report.cash_check == "ok":
        return "✓ Adds up"
    if report.cash_check == "off" and report.cash_diff is not None:
        return f"⚠ Off by {money(abs(report.cash_diff))}"
    return "–"


def files_rows(result: IngestResult) -> list[dict[str, Any]]:
    """Oldest period first; files we couldn't read go last. (Browsers read uploads in no fixed order.)"""

    def order(f: FileReport) -> tuple[bool, date, str]:
        return (f.status == "bad", f.period[0] if f.period else date.max, f.name)

    return [
        {
            "file": f.name,
            "period": period_label(*f.period) if f.period else "–",
            "rows": f.rows,
            "skipped": f.skipped,
            "cash": _cash_label(f),
            "status": _STATUS[f.status],
        }
        for f in sorted(result.files, key=order)
    ]


def coverage_rows(result: IngestResult) -> list[dict[str, Any]]:
    spans = sorted(f.period for f in result.loaded_files if f.period)
    if not spans:
        return []
    last_day = max(end for _, end in spans)
    rows: list[dict[str, Any]] = []
    for year in range(spans[0][0].year, last_day.year + 1):
        first, last = date(year, 1, 1), date(year, 12, 31)
        days = (last - first).days + 1
        covered: set[int] = set()
        for start, end in spans:
            lo, hi = max(start, first), min(end, last)
            covered.update(range(lo.toordinal(), hi.toordinal() + 1))
        share = round(len(covered) / days, 3)
        if not covered:
            label = "Missing"
        elif share >= 0.98:
            share, label = 1.0, "Full year"
        else:
            lo_m, hi_m = date.fromordinal(min(covered)).month, date.fromordinal(max(covered)).month
            label = f"{MONTHS[lo_m - 1]}–{MONTHS[hi_m - 1]}" + (" (so far)" if year == last_day.year else "")
        rows.append({"year": year, "share": share, "label": label})
    return rows


def issue_items(result: IngestResult, catalog: MessageCatalog, extra: Sequence[Issue] = ()) -> html.Ul:
    ordered = sorted([*result.all_issues(), *extra], key=lambda i: _SEVERITY_RANK[i.severity])
    issues = [i for i in ordered if i.severity is not Severity.BLOCKING]
    if not issues:
        return html.Ul(
            html.Li(
                [html.Span("✓", className="fx-ic"), html.Span("Everything checks out.")],
                className="fx-issue fx-sev-good",
            ),
            className="fx-issues",
        )
    return html.Ul(
        [
            html.Li(
                [
                    html.Span(_ICON[i.severity], className="fx-ic", **aria(hidden="true")),
                    html.Span(catalog.render(i).text),
                ],
                className=f"fx-issue fx-sev-{i.severity.value}",
            )
            for i in issues
        ],
        className="fx-issues",
    )


def transaction_rows(result: IngestResult, type_filter: str | None, search: str) -> list[dict[str, Any]]:
    needle = (search or "").strip().upper()
    rows: list[dict[str, Any]] = []
    for t in reversed(result.transactions):
        if type_filter and t.raw_type != type_filter:
            continue
        if needle and needle not in (t.symbol or ""):
            continue
        rows.append(
            {
                "date": t.date.isoformat(),
                "type": t.raw_type,
                "symbol": t.symbol or "–",
                "quantity": t.quantity or None,
                "price": t.price,
                "gross": t.gross,
                "commission": t.commission or None,
                "net": t.net,
                "file": t.source_file,
            }
        )
    return rows


def template_download(settings: Settings) -> dict[str, Any]:
    return dict(send_string(template_csv(settings.format), TEMPLATE_FILENAME, type="text/csv"))


def column_guide_table(settings: Settings) -> Any:
    rows = [asdict(g) for g in column_guide(settings.format)]
    return data_table("column-guide-grid", GUIDE_COLS, rows)


def explain(result: IngestResult, catalog: MessageCatalog, date_format: str) -> html.Div:
    """Explain panel for the Data Hub once files are loaded."""
    span = result.date_range
    warnings = [i for i in result.all_issues() if i.severity is Severity.WARNING]
    covering = (
        f" covering {display_date(span[0], date_format)} to {display_date(span[1], date_format)}"
        if span
        else ""
    )
    files = plural(len(result.loaded_files), "file")
    rows = plural(len(result.transactions), "transaction")
    holdings = plural(len(result.tickers), "stock or fund", "stocks and funds")
    needs = plural(len(warnings), "thing needs", "things need")
    attention = (
        f"{needs} your attention, listed under “Things to know”." if warnings else "Everything checks out."
    )
    nutshell = f"You've loaded {files}{covering}, with {rows} in {holdings}. {attention}"
    parts = [
        (
            "Template and column guide",
            "A template with placeholders like <TICKER SYMBOL> to replace with your "
            "own data, plus a guide to what each column expects.",
        ),
        ("Upload box", "Drop in one CSV file per year. FolioEx reads them on your computer only."),
        (
            "Your files",
            "One row per file: the period it covers, how many rows we read, and whether its cash adds up.",
        ),
        ("Years covered", "Which years your files cover, so you can spot a missing year."),
        ("Things to know", "Anything we skipped or that looks incomplete, in plain English."),
        ("All transactions", "Every row we read, newest first. Filter by type or search for a ticker."),
    ]
    glossary = [
        (
            "Transaction history",
            "Everything that happened in your account: buys, sells, dividends, deposits and fees.",
        ),
        (
            "Cash check",
            "Each file states your starting and ending cash. We make sure every transaction adds up to it.",
        ),
        ("Duplicate rows", "When two files overlap, the same transaction appears twice. We count it once."),
    ]
    body: list[Any] = [
        html.Div([html.H3("In a nutshell"), html.P(nutshell, className="fx-nutshell")]),
        html.Div(
            [html.H3("What each part shows"), html.Dl([html.Div([html.Dt(t), html.Dd(d)]) for t, d in parts])]
        ),
    ]
    if warnings:
        body.append(
            html.Div(
                [
                    html.H3("Things to keep an eye on"),
                    html.Ul([html.Li(catalog.render(i).text) for i in warnings[:3]]),
                ]
            )
        )
    body.append(
        html.Div(
            [html.H3("Words to know"), html.Dl([html.Div([html.Dt(t), html.Dd(d)]) for t, d in glossary])]
        )
    )
    body.append(html.P(catalog.text("explain_footer"), className="fx-explain-foot"))
    return html.Div(body)


# ---------- layout ----------


def _coverage_bars(rows: list[dict[str, Any]]) -> list[Any]:
    return [
        html.Div(
            [
                html.Span(str(r["year"]), className="fx-cov-year"),
                html.Div(
                    html.Span(style={"width": f"{r['share'] * 100:.0f}%"}),
                    className="fx-cov-bar",
                    **aria(hidden="true"),
                ),
                html.Span(r["label"], className="fx-cov-label"),
            ],
            className="fx-cov-row",
        )
        for r in rows
    ]


def _progress_card() -> html.Div:
    legend = [
        ("var(--c-up)", "Done (green or red is just for fun)"),
        ("var(--c-now)", "Working now"),
        ("var(--c-pend)", "Waiting"),
    ]
    return html.Div(
        [
            html.Div(
                [html.H2("Crunching your numbers"), html.Span(id="progress-pct", className="fx-muted")],
                className="fx-card-head",
            ),
            html.Div(
                dcc.Graph(id="progress-candles", config={"displayModeBar": False}, className="fx-candles"),
                id="progress-figure-wrap",
                role="img",
                **aria(label="Processing progress"),
            ),
            html.P(id="progress-caption", className="fx-caption", **aria(live="polite")),
            html.Div(
                [
                    html.Span([html.Span(className="fx-swatch", style={"background": color}), text])
                    for color, text in legend
                ],
                className="fx-legend",
            ),
            html.Div(
                dbc.Button("Try again", id="progress-retry", className="fx-btn-primary", n_clicks=0),
                id="progress-retry-wrap",
                hidden=True,
            ),
        ],
        id="progress-card",
        className="fx-card",
        hidden=True,
    )


def layout(settings: Settings, catalog: MessageCatalog) -> html.Div:
    up = settings.upload
    example_date = display_date(date(2026, 12, 31), settings.format.date_format)
    start_card = html.Div(
        [
            html.H2("No file? Start here"),
            html.P(
                "Download our template and replace each placeholder with your own data. It opens in Excel or "
                "any spreadsheet app. Save it as CSV when you're done.",
                className="fx-muted",
            ),
            html.Div(
                [
                    dbc.Button(
                        "⬇ Download template",
                        id="template-download-btn",
                        className="fx-btn-primary",
                        n_clicks=0,
                    ),
                    dbc.Button("Column guide", id="column-guide-btn", className="fx-btn", n_clicks=0),
                ],
                className="fx-btn-row fx-left",
            ),
            dcc.Download(id="template-download"),
            html.Details(
                [
                    html.Summary("How to fill it in"),
                    html.Ul(
                        [
                            html.Li(
                                "Replace every placeholder in angle brackets, like <TICKER SYMBOL>, "
                                "with your own value. Delete rows for transaction types you don't need."
                            ),
                            html.Li(
                                "Use ticker symbols as shown on your broker statement, never company names."
                            ),
                            html.Li(f"Dates look like {example_date}."),
                            html.Li(f"Use {settings.format.null_token} for anything that doesn't apply."),
                            html.Li(
                                "Buys: quantity positive, net amount negative. "
                                "Sells: quantity negative, net positive."
                            ),
                            html.Li(
                                "Dividends and interest are positive. Fees and tax withheld are negative."
                            ),
                            html.Li("Only the transaction section is required."),
                        ]
                    ),
                ]
            ),
            html.Details(
                [
                    html.Summary("How to export your transaction history"),
                    html.Ol(
                        [
                            html.Li("Sign in to your broker's website."),
                            html.Li("Open the reports or statements area and choose Transaction History."),
                            html.Li("Pick one year at a time and download it as CSV."),
                            html.Li("Repeat for every year back to when you opened the account."),
                        ]
                    ),
                ]
            ),
        ],
        className="fx-card",
    )
    upload_card = html.Div(
        [
            html.H2("Upload your files"),
            dcc.Upload(
                html.Div(
                    [
                        html.Span("⇪ Drag and drop CSV files here", className="fx-drop-big"),
                        html.Span(["or ", html.U("browse your computer")]),
                        html.Span(
                            f"Up to {up.max_files} files · {up.max_file_mb:g} MB each · "
                            f"{up.max_total_mb:g} MB total",
                            className="fx-muted fx-small",
                        ),
                    ],
                    className="fx-drop-inner",
                ),
                # No `accept` filter: the browser would silently drop other files; we explain instead.
                id="upload",
                multiple=True,
                className="fx-drop",
            ),
            html.Div(
                dbc.Button("▶ Load sample data", id="load-sample", className="fx-btn", n_clicks=0),
                className="fx-btn-row fx-left",
            ),
            dcc.Loading(html.Div(id="upload-msg", className="fx-upload-msg"), type="dot"),
        ],
        className="fx-card",
    )
    report = html.Div(
        [
            html.Div([html.H2("Your files"), data_table("files-grid", FILE_COLS, [])], className="fx-card"),
            html.Div(
                [
                    html.Div(
                        [html.H2("Years covered"), html.Div(id="coverage", className="fx-cov")],
                        className="fx-card",
                    ),
                    html.Div(
                        [html.H2("Things to know", id="issues-title"), html.Div(id="issues")],
                        className="fx-card",
                    ),
                ],
                className="fx-grid-2",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.H2("All transactions", id="tx-title"),
                            dbc.Button(
                                "Clear data", id="clear-data", className="fx-btn fx-danger", n_clicks=0
                            ),
                        ],
                        className="fx-card-head",
                    ),
                    html.Div(
                        [
                            html.Label("Transaction type", htmlFor="tx-type", className="fx-sr-only"),
                            dbc.Select(
                                id="tx-type",
                                options=[{"label": "All types", "value": ALL_TYPES}],
                                value=ALL_TYPES,
                                className="fx-select",
                            ),
                            html.Label("Search symbol", htmlFor="tx-search", className="fx-sr-only"),
                            dbc.Input(
                                id="tx-search",
                                placeholder="Search symbol…",
                                debounce=0.3,
                                value="",
                                className="fx-input",
                            ),
                        ],
                        className="fx-filters",
                    ),
                    data_table("tx-grid", TX_COLS, [], page_size=15),
                ],
                className="fx-card",
            ),
        ],
        id="datahub-report",
        className="fx-panel",
        hidden=True,
    )
    guide_modal = dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Column guide")),
            dbc.ModalBody(
                [
                    html.P("What each column in the template expects.", className="fx-muted"),
                    column_guide_table(settings),
                ]
            ),
            dbc.ModalFooter(
                dbc.Button("Close", id="column-guide-close", className="fx-btn-primary", n_clicks=0)
            ),
        ],
        id="column-guide-modal",
        is_open=False,
        size="xl",
        centered=True,
        scrollable=True,
    )
    empty = html.Div(
        [
            html.P("No files loaded yet", className="fx-lead"),
            html.P("Upload your transaction history above to build your dashboards.", className="fx-muted"),
        ],
        id="datahub-empty",
        className="fx-card fx-empty",
    )
    return html.Div(
        [
            panel_header("Data Hub", "Bring in your transactions", "datahub"),
            html.Div([start_card, upload_card], className="fx-grid-2"),
            _progress_card(),
            dcc.Store(id="job-id"),
            dcc.Interval(id="progress-poll", interval=settings.progress.poll_ms, disabled=True),
            empty,
            report,
            guide_modal,
        ],
        id="panel-datahub",
        className="fx-panel",
    )


# ---------- callbacks ----------


def _start_fallback(message: UserMessage) -> tuple[Any, ...]:
    return no_update, True, True, friendly_alert(message), True


def _sample_fallback(message: UserMessage) -> tuple[Any, ...]:
    return no_update, True, True, friendly_alert(message), True, "datahub"


def _poll_fallback(message: UserMessage) -> tuple[Any, ...]:
    return (no_update,) * 4 + (True, no_update, friendly_alert(message), False)


def _quiet(message: UserMessage) -> Any:
    raise PreventUpdate


def register(app: Dash, settings: Settings, market: CachedMarketData) -> None:
    catalog = default_catalog()

    JOBS.configure(min_stage_ms=settings.progress.min_stage_ms)

    @app.callback(
        Output("job-id", "data"),
        Output("progress-poll", "disabled"),
        Output("progress-card", "hidden"),
        Output("upload-msg", "children"),
        Output("progress-retry-wrap", "hidden"),
        Input("upload", "contents"),
        State("upload", "filename"),
        prevent_initial_call=True,
    )
    @safe_callback(_start_fallback)
    def on_upload(contents: list[str] | None, names: list[str] | None) -> tuple[Any, ...]:
        if not contents:
            raise PreventUpdate
        uploads = decode_uploads(contents, names or [])
        job_id = JOBS.start(lambda progress: process(uploads, settings, progress, market=market))
        return job_id, False, False, [], True

    @app.callback(
        Output("job-id", "data", allow_duplicate=True),
        Output("progress-poll", "disabled", allow_duplicate=True),
        Output("progress-card", "hidden", allow_duplicate=True),
        Output("upload-msg", "children", allow_duplicate=True),
        Output("progress-retry-wrap", "hidden", allow_duplicate=True),
        Output("tabs", "active_tab", allow_duplicate=True),
        Input("load-sample", "n_clicks"),
        Input({"type": "load-sample", "from": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_sample_fallback)
    def load_sample(here: int | None, elsewhere: list[int | None]) -> tuple[Any, ...]:
        if not here and not any(elsewhere or []):
            raise PreventUpdate
        # Reading the bundled files happens inside the job, so a missing file is reported like any failure.
        job_id = JOBS.start(lambda progress: process(sample_uploads(), settings, progress, market=market))
        return job_id, False, False, [], True, "datahub"

    @app.callback(
        Output("job-id", "data", allow_duplicate=True),
        Output("progress-poll", "disabled", allow_duplicate=True),
        Output("upload-msg", "children", allow_duplicate=True),
        Output("progress-retry-wrap", "hidden", allow_duplicate=True),
        Input("progress-retry", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def retry(_: int | None) -> tuple[Any, ...]:
        job_id = JOBS.retry()
        if job_id is None:
            raise PreventUpdate
        return job_id, False, [], True

    @app.callback(
        Output("progress-candles", "figure"),
        Output("progress-caption", "children"),
        Output("progress-pct", "children"),
        Output("progress-figure-wrap", "aria-label"),
        Output("progress-poll", "disabled", allow_duplicate=True),
        Output("dataset-id", "data"),
        Output("upload-msg", "children", allow_duplicate=True),
        Output("progress-retry-wrap", "hidden", allow_duplicate=True),
        Input("progress-poll", "n_intervals"),
        Input("theme-store", "data"),
        State("job-id", "data"),
        State("theme-default", "data"),
        State("motion", "data"),
        prevent_initial_call=True,
    )
    @safe_callback(_poll_fallback)
    def poll(
        ticks: int | None,
        theme: str | None,
        job_id: str | None,
        default_theme: str | None,
        still: bool | None,
    ) -> tuple[Any, ...]:
        snap = JOBS.snapshot(job_id)
        if snap is None:
            raise PreventUpdate
        pulse = bool((ticks or 0) % 2) and not still
        view = progress_view(
            snap, theme=theme or default_theme, pulse=pulse, green_ratio=settings.progress.green_ratio
        )
        shown = (view.figure, view.caption, view.percent_text, view.aria_label)
        if ctx.triggered_id == "theme-store" or snap.status == "running":
            return *shown, snap.status != "running", no_update, no_update, no_update
        if snap.status == "done":
            result = JOBS.result(job_id)
            if result is None:
                raise PreventUpdate
            dataset_id, message = finish_processing(result)
            return *shown, True, dataset_id or no_update, message, True
        if snap.status == "failed":
            issue = snap.issue or Issue(IssueKind.PROCESSING_FAILED, Severity.BLOCKING)
            return *shown, True, no_update, friendly_alert(catalog.render(issue)), False
        return *shown, True, no_update, no_update, True  # cancelled: a newer job took over

    @app.callback(
        Output("datahub-report", "hidden"),
        Output("datahub-empty", "hidden"),
        Output("files-grid", "rowData"),
        Output("coverage", "children"),
        Output("issues", "children"),
        Output("issues-title", "children"),
        Output("tx-title", "children"),
        Output("tx-type", "options"),
        Output("files-chip", "children"),
        Output("files-chip", "className"),
        Input("dataset-id", "data"),
    )
    @safe_callback(_quiet)
    def show_report(dataset_id: str | None) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        if data is None:
            return (
                True,
                False,
                [],
                [],
                [],
                "Things to know",
                "All transactions",
                [{"label": "All types", "value": ALL_TYPES}],
                "No files loaded",
                "fx-chip",
            )
        result = data.ingest
        notes = [i for i in data.all_issues() if i.severity is not Severity.BLOCKING]
        span = result.date_range
        span_text = (
            (
                f" · {display_date(span[0], settings.format.date_format)} → "
                f"{display_date(span[1], settings.format.date_format)}"
            )
            if span
            else ""
        )
        types = sorted({t.raw_type for t in result.transactions})
        loaded = len(result.loaded_files)
        return (
            False,
            True,
            files_rows(result),
            _coverage_bars(coverage_rows(result)),
            issue_items(result, catalog, data.analysis_issues()),
            f"Things to know ({len(notes)})" if notes else "Things to know",
            f"All transactions · {plural(len(result.transactions), 'row')}{span_text} · "
            f"{plural(len(result.tickers), 'stock or fund', 'stocks and funds')}",
            [{"label": "All types", "value": ALL_TYPES}, *({"label": t, "value": t} for t in types)],
            f"{plural(loaded, 'file')} loaded",
            "fx-chip ok",
        )

    @app.callback(
        Output("tx-grid", "rowData"),
        Input("dataset-id", "data"),
        Input("tx-type", "value"),
        Input("tx-search", "value"),
    )
    @safe_callback(_quiet)
    def filter_transactions(dataset_id: str | None, type_filter: str | None, search: str | None) -> list[Any]:
        data = REGISTRY.get(dataset_id)
        result = data.ingest if data else None
        chosen = None if type_filter in (None, "", ALL_TYPES) else type_filter
        return transaction_rows(result, chosen, search or "") if result else []

    @app.callback(
        Output("template-download", "data"),
        Input("template-download-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def send_template(_: int | None) -> dict[str, Any]:
        return template_download(settings)

    @app.callback(
        Output("column-guide-modal", "is_open"),
        Input("column-guide-btn", "n_clicks"),
        Input("column-guide-close", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def toggle_guide(_open: int | None, _close: int | None) -> bool:
        return bool(ctx.triggered_id == "column-guide-btn")

    @app.callback(
        Output("dataset-id", "data", allow_duplicate=True),
        Output("upload-msg", "children", allow_duplicate=True),
        Output("progress-card", "hidden", allow_duplicate=True),
        Output("progress-poll", "disabled", allow_duplicate=True),
        Input("clear-data", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def clear(_: int | None) -> tuple[Any, ...]:
        JOBS.cancel()
        REGISTRY.clear()
        message = UserMessage("Session data cleared. Nothing was saved to disk.", Severity.INFO)
        return None, friendly_alert(message), True, True
