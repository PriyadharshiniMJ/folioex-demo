"""Many files → one clean, ordered history plus a per-file report.

Never all-or-nothing: a bad file is reported and skipped, the rest still load.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from folioex.config import Settings
from folioex.domain.models import POSITION_TYPES, Transaction, TxnType
from folioex.errors import FolioExError, Issue, IssueKind, Severity
from folioex.formatting import display_date, money, quantity
from folioex.ingestion.transaction_history import ParsedFile, Summary, parse_file
from folioex.progress import NullProgress, Progress

logger = logging.getLogger("folioex.ingestion")

MB = 1024 * 1024
_CASH_TOLERANCE = 0.01
_GAP_DAYS = 7
_SEVERITY_ORDER = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}

Status = Literal["ok", "warn", "bad"]
CashCheck = Literal["ok", "off", "n/a"]


@dataclass(frozen=True, slots=True)
class Upload:
    name: str
    content: bytes


@dataclass(slots=True)
class FileReport:
    name: str
    status: Status
    issues: list[Issue] = field(default_factory=list)
    period: tuple[date, date] | None = None
    period_from_statement: bool = False
    rows: int | None = None
    skipped: int | None = None
    cash_check: CashCheck = "n/a"
    cash_diff: float | None = None
    summary: Summary | None = None


@dataclass(slots=True)
class IngestResult:
    transactions: list[Transaction]
    files: list[FileReport]
    issues: list[Issue]  # dataset-level: duplicates, gaps, continuity, coverage, limits

    def all_issues(self) -> list[Issue]:
        everything = [*self.issues, *(i for f in self.files for i in f.issues)]
        return sorted(everything, key=lambda i: _SEVERITY_ORDER[i.severity])

    @property
    def tickers(self) -> list[str]:
        return sorted({t.symbol for t in self.transactions if t.symbol})

    @property
    def date_range(self) -> tuple[date, date] | None:
        if not self.transactions:
            return None
        return self.transactions[0].date, self.transactions[-1].date

    @property
    def loaded_files(self) -> list[FileReport]:
        return [f for f in self.files if f.status != "bad"]


def _mb(value: float) -> str:
    return quantity(value)


def ingest(uploads: Sequence[Upload], settings: Settings, progress: Progress | None = None) -> IngestResult:
    progress = progress or NullProgress()
    limits, fmt = settings.upload, settings.format
    dataset_issues: list[Issue] = []
    if len(uploads) > limits.max_files:
        dataset_issues.append(Issue(IssueKind.TOO_MANY_FILES, Severity.WARNING, {"limit": limits.max_files}))
        uploads = uploads[: limits.max_files]

    progress.stage("read_files")
    reports: list[FileReport] = []
    parsed: list[tuple[ParsedFile, FileReport]] = []
    total = 0
    for done, upload in enumerate(uploads, start=1):
        rejected = _screen(upload, total, settings)
        if rejected is not None:
            reports.append(rejected)
        else:
            total += len(upload.content)
            report = _read(upload, settings)
            reports.append(report[1] if isinstance(report, tuple) else report)
            if isinstance(report, tuple):
                parsed.append(report)
        progress.sub(done, len(uploads), "files")
    # Sections, headers, dates, types and symbols are all handled while each file is read.
    for key in ("detect_sections", "validate_headers", "parse_dates", "map_types", "normalize_symbols"):
        progress.stage(key)

    progress.stage("merge_files")
    ordered = sorted(parsed, key=lambda pr: _start(pr[1]) or date.max)
    progress.stage("remove_overlaps")
    merged, duplicates = _merge([p for p, _ in parsed])
    if duplicates:
        dataset_issues.append(Issue(IssueKind.DUPLICATES_IGNORED, Severity.INFO, {"count": duplicates}))
    transactions = sorted([*_opening_deposit(ordered), *merged], key=Transaction.sort_key)
    progress.stage("reconcile_cash")
    dataset_issues += _continuity(ordered, fmt.date_format)
    progress.stage("detect_gaps")
    dataset_issues += _history_gaps(transactions, fmt.date_format)

    for _, report in parsed:
        report.status = "warn" if any(i.severity is not Severity.INFO for i in report.issues) else "ok"
    return IngestResult(transactions, reports, dataset_issues)


def _screen(upload: Upload, total_so_far: int, settings: Settings) -> FileReport | None:
    """Reject a file before reading it: wrong type or over a size limit."""
    limits, name, size = settings.upload, upload.name, len(upload.content)
    if not name.lower().endswith(".csv"):
        return _bad(name, IssueKind.FILE_NOT_CSV, file=name)
    if size > limits.max_file_mb * MB:
        return _bad(name, IssueKind.FILE_TOO_LARGE, file=name, limit_mb=_mb(limits.max_file_mb))
    if total_so_far + size > limits.max_total_mb * MB:
        return _bad(name, IssueKind.TOTAL_TOO_LARGE, file=name, limit_mb=_mb(limits.max_total_mb))
    return None


def _bad(name: str, kind: IssueKind, **context: object) -> FileReport:
    return FileReport(name, "bad", [Issue(kind, Severity.BLOCKING, context)])


def _read(upload: Upload, settings: Settings) -> FileReport | tuple[ParsedFile, FileReport]:
    try:
        pf = parse_file(upload.name, upload.content, settings.format)
    except FolioExError as exc:
        return FileReport(upload.name, "bad", [exc.issue])
    except Exception:
        logger.exception("Unexpected problem reading %s", upload.name)
        return _bad(upload.name, IssueKind.UNEXPECTED)
    report = FileReport(
        name=upload.name,
        status="ok",
        issues=list(pf.issues),
        period=pf.period or _data_range(pf.transactions),
        period_from_statement=pf.period is not None,
        rows=pf.rows_read,
        skipped=pf.rows_skipped,
        summary=pf.summary,
    )
    _reconcile(pf, report)
    return pf, report


def _data_range(txns: Sequence[Transaction]) -> tuple[date, date] | None:
    if not txns:
        return None
    dates = [t.date for t in txns]
    return min(dates), max(dates)


def _start(report: FileReport) -> date | None:
    return report.period[0] if report.period else None


def _reconcile(pf: ParsedFile, report: FileReport) -> None:
    summary = pf.summary
    if summary is None or summary.starting_cash is None or summary.ending_cash is None:
        return
    expected = summary.starting_cash + sum(t.net for t in pf.transactions)
    diff = summary.ending_cash - expected
    report.cash_diff = diff
    if abs(diff) > _CASH_TOLERANCE:
        report.cash_check = "off"
        report.issues.append(
            Issue(IssueKind.CASH_MISMATCH, Severity.WARNING, {"file": pf.name, "amount": money(abs(diff))})
        )
    else:
        report.cash_check = "ok"


def _merge(files: Sequence[ParsedFile]) -> tuple[list[Transaction], int]:
    """Overlapping exports: each distinct row counts as many times as the most it appears in ONE file.

    That removes copies across files while keeping genuine repeats inside a file (e.g. partial fills).
    """
    best: dict[tuple[object, ...], tuple[int, int]] = {}
    for index, pf in enumerate(files):
        for key, count in Counter(t.key() for t in pf.transactions).items():
            if count > best.get(key, (0, -1))[0]:
                best[key] = (count, index)
    merged: list[Transaction] = []
    for index, pf in enumerate(files):
        taken: Counter[tuple[object, ...]] = Counter()
        for t in pf.transactions:
            key = t.key()
            count, owner = best[key]
            if owner == index and taken[key] < count:
                merged.append(t)
                taken[key] += 1
    total = sum(len(pf.transactions) for pf in files)
    return merged, total - len(merged)


def _opening_deposit(ordered: Sequence[tuple[ParsedFile, FileReport]]) -> list[Transaction]:
    """Cash already in the account before the first file counts as money put in on that file's first day."""
    if not ordered:
        return []
    pf, report = ordered[0]
    start = _start(report)
    if pf.summary is None or not pf.summary.starting_cash or pf.summary.starting_cash <= 0 or start is None:
        return []
    cash = pf.summary.starting_cash
    return [
        Transaction(
            start, TxnType.DEPOSIT, "Opening cash", None, 0.0, None, None, cash, 0.0, cash, None, pf.name, 0
        )
    ]


def _continuity(ordered: Sequence[tuple[ParsedFile, FileReport]], date_format: str) -> list[Issue]:
    issues: list[Issue] = []
    with_periods = [(pf, r) for pf, r in ordered if r.period_from_statement and r.period]
    covered_until: date | None = None
    previous: tuple[ParsedFile, FileReport] | None = None
    for pf, report in with_periods:
        assert report.period is not None
        start, end = report.period
        if covered_until is not None and start > covered_until + timedelta(days=_GAP_DAYS):
            issues.append(
                Issue(
                    IssueKind.COVERAGE_GAP,
                    Severity.WARNING,
                    {
                        "start": display_date(covered_until + timedelta(days=1), date_format),
                        "end": display_date(start - timedelta(days=1), date_format),
                    },
                )
            )
        if previous is not None:
            before = previous[0].summary
            if (
                before is not None
                and pf.summary is not None
                and before.ending_cash is not None
                and pf.summary.starting_cash is not None
                and abs(before.ending_cash - pf.summary.starting_cash) > _CASH_TOLERANCE
            ):
                issues.append(
                    Issue(
                        IssueKind.CASH_CONTINUITY,
                        Severity.WARNING,
                        {"previous": previous[0].name, "file": pf.name},
                    )
                )
        covered_until = max(covered_until or end, end)
        previous = (pf, report)
    return issues


def _history_gaps(transactions: Sequence[Transaction], date_format: str) -> list[Issue]:
    """Spot history that starts too late: selling shares never bought, or dividends on shares never held."""
    held: defaultdict[str, float] = defaultdict(float)
    flagged_sells: set[str] = set()
    flagged_dividends: set[str] = set()
    issues: list[Issue] = []
    for t in transactions:
        if t.symbol is None:
            continue
        if t.type in POSITION_TYPES:
            after = held[t.symbol] + t.quantity
            if after < -1e-6:
                if t.symbol not in flagged_sells:
                    flagged_sells.add(t.symbol)
                    issues.append(
                        Issue(
                            IssueKind.HISTORY_GAP,
                            Severity.WARNING,
                            {
                                "symbol": t.symbol,
                                "shares": quantity(-after),
                                "date": display_date(t.date, date_format),
                            },
                        )
                    )
                after = 0.0
            held[t.symbol] = after
        elif t.type is TxnType.DIVIDEND and held[t.symbol] <= 1e-9 and t.symbol not in flagged_dividends:
            flagged_dividends.add(t.symbol)
            issues.append(
                Issue(
                    IssueKind.DIVIDEND_WITHOUT_SHARES,
                    Severity.WARNING,
                    {"symbol": t.symbol, "date": display_date(t.date, date_format)},
                )
            )
    return issues
