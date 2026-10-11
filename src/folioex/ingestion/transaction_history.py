"""Read one transaction-history file into canonical transactions plus plain-English issues.

Every format detail (column names, type labels, date format, null token, symbol overrides) comes from
``FormatCfg``. Bad rows are skipped and counted; only a missing section or column stops a file.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from folioex.config import FormatCfg
from folioex.domain.models import POSITION_TYPES, Transaction, TxnType
from folioex.errors import FolioExError, Issue, IssueKind, Severity
from folioex.ingestion.sectioned_csv import Section, SectionRow, decode, norm, split_sections
from folioex.ingestion.symbols import normalize_symbol

logger = logging.getLogger("folioex.ingestion")

PLACEHOLDER = re.compile(r"^<[^<>]*>$")
NON_CASH: frozenset[TxnType] = frozenset({TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
INCOMING: frozenset[TxnType] = frozenset({TxnType.BUY, TxnType.TRANSFER_IN})
OUTGOING: frozenset[TxnType] = frozenset({TxnType.SELL, TxnType.TRANSFER_OUT})
PERIOD_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d")
_TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class Summary:
    starting_cash: float | None
    ending_cash: float | None
    base_currency: str | None


@dataclass(slots=True)
class ParsedFile:
    name: str
    transactions: list[Transaction]
    issues: list[Issue]
    period: tuple[date, date] | None
    summary: Summary | None
    rows_read: int
    rows_skipped: int


class _BadNumber(ValueError):
    def __init__(self, cell: str) -> None:
        super().__init__(cell)
        self.cell = cell


def parse_number(cell: str | None, null_token: str) -> float | None:
    """1,234.50 · $1,234.50 · (12.30) · −7.5 · '-' (empty). Raises ``_BadNumber`` otherwise."""
    if cell is None:
        return None
    text = cell.strip()
    if text in ("", null_token):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("−", "-").replace("$", "").replace(",", "").replace(" ", "")
    try:
        value = float(cleaned)
    except ValueError:
        raise _BadNumber(text) from None
    if not math.isfinite(value):
        raise _BadNumber(text)
    return -value if negative else value


@dataclass(slots=True)
class _Tally:
    """Counts of skipped or noteworthy rows, with the first example of each."""

    counts: Counter[IssueKind] = field(default_factory=Counter)
    examples: dict[IssueKind, str] = field(default_factory=dict)
    unknown_labels: Counter[str] = field(default_factory=Counter)

    def add(self, kind: IssueKind, example: str = "") -> None:
        self.counts[kind] += 1
        self.examples.setdefault(kind, example)


_SKIPS = (
    IssueKind.MASKED_ROWS,
    IssueKind.BAD_DATES,
    IssueKind.MONTH_DAY_DATES,
    IssueKind.NON_USD_ROWS,
    IssueKind.UNPARSEABLE_ROWS,
)


def _swapped_day_month(date_format: str) -> str | None:
    if "%d" not in date_format or "%m" not in date_format:
        return None
    return date_format.replace("%d", "\0").replace("%m", "%d").replace("\0", "%m")


def _parse_date(cell: str, date_format: str) -> date:
    return datetime.strptime(cell.strip(), date_format).date()


def _parse_period(text: str | None, date_format: str) -> tuple[date, date] | None:
    if not text or " - " not in text:
        return None
    left, right = (part.strip() for part in text.split(" - ", 1))
    for fmt in (*PERIOD_FORMATS, date_format):
        try:
            return _parse_date(left, fmt), _parse_date(right, fmt)
        except ValueError:
            continue
    return None


def _field_rows(section: Section | None) -> dict[str, str]:
    """``Field Name`` / ``Field Value`` pairs, keyed by normalized field name."""
    if section is None:
        return {}
    pairs: dict[str, str] = {}
    for row in section.rows:
        values = list(row.values.values())
        if len(values) >= 2:
            pairs[norm(values[0])] = values[1]
    return pairs


def _summary(section: Section | None, fmt: FormatCfg) -> Summary | None:
    if section is None:
        return None
    fields = _field_rows(section)

    def cash(name: str) -> float | None:
        raw = fields.get(name)
        if raw is None or PLACEHOLDER.match(raw):
            return None
        try:
            return parse_number(raw, fmt.null_token)
        except _BadNumber:
            return None

    return Summary(cash("starting cash"), cash("ending cash"), fields.get("base currency"))


def _find(sections: dict[str, Section], name: str) -> Section | None:
    wanted = norm(name)
    return next((s for key, s in sections.items() if norm(key) == wanted), None)


def parse_file(name: str, raw: bytes, fmt: FormatCfg) -> ParsedFile:
    cols = fmt.columns
    text = decode(raw, name)
    sections = split_sections(
        text, plain_section=fmt.section, plain_required=(cols.date, cols.type, cols.net)
    )
    section = _find(sections, fmt.section)
    if section is None:
        raise FolioExError(IssueKind.NO_TRANSACTIONS_SECTION, file=name)
    for required in (cols.date, cols.type, cols.symbol, cols.quantity, cols.net):
        if not section.has_column(required):
            raise FolioExError(IssueKind.MISSING_COLUMN, file=name, column=required)

    reader = _RowReader(name, section, fmt)
    transactions = [t for t in (reader.read(row) for row in section.rows) if t is not None]
    statement = _field_rows(_find(sections, fmt.statement_section))
    return ParsedFile(
        name=name,
        transactions=transactions,
        issues=_issues(name, reader),
        period=_parse_period(statement.get("period"), fmt.date_format),
        summary=_summary(_find(sections, fmt.summary_section), fmt),
        rows_read=len(transactions),
        rows_skipped=sum(reader.tally.counts[k] for k in _SKIPS),
    )


class _Skip(Exception):  # control flow for unusable rows, never shown to anyone
    """This row can't be used; it is counted under ``kind`` with an example cell."""

    def __init__(self, kind: IssueKind, example: str = "") -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.example = example


@dataclass(frozen=True, slots=True)
class _Amounts:
    quantity: float | None
    price: float | None
    gross: float
    commission: float
    net: float
    mismatched: bool


class _RowReader:
    """Turns section rows into transactions, one small step per concern, tallying what it skips."""

    def __init__(self, file: str, section: Section, fmt: FormatCfg) -> None:
        self.file, self.section, self.fmt = file, section, fmt
        self.types = {norm(label): category for label, category in fmt.types.items()}
        self.tally = _Tally()
        self.placeholders = self.sign_mismatch = self.net_mismatch = 0

    def read(self, row: SectionRow) -> Transaction | None:
        try:
            return self._read(row)
        except _Skip as skip:
            if skip.kind is IssueKind.TEMPLATE_ROWS_SKIPPED:
                self.placeholders += 1
            else:
                self.tally.add(skip.kind, skip.example)
            return None

    def _get(self, row: SectionRow, column: str) -> str:
        return self.section.get(row, column) or ""

    def _read(self, row: SectionRow) -> Transaction:
        cols = self.fmt.columns
        self._screen(row)
        when = self._date(self._get(row, cols.date))
        label = " ".join(self._get(row, cols.type).split())
        category = self._category(label)
        currency = self._currency(self._get(row, cols.currency))
        amounts = self._amounts(row, category)
        symbol = normalize_symbol(self._get(row, cols.symbol), self.fmt.symbols, self.fmt.null_token)
        change = self._share_change(category, amounts.quantity, symbol, row)
        self.net_mismatch += amounts.mismatched
        account = self._get(row, cols.account).strip()
        return Transaction(
            date=when,
            type=category,
            raw_type=label,
            symbol=symbol,
            quantity=change,
            price=amounts.price,
            currency=currency,
            gross=amounts.gross,
            commission=amounts.commission,
            net=amounts.net,
            account=None if account in ("", self.fmt.null_token) else account,
            source_file=self.file,
            line=row.line,
        )

    def _screen(self, row: SectionRow) -> None:
        c = self.fmt.columns
        used = [
            self._get(row, name)
            for name in (
                c.date,
                c.type,
                c.symbol,
                c.quantity,
                c.price,
                c.currency,
                c.gross,
                c.commission,
                c.net,
            )
        ]
        if any(PLACEHOLDER.match(cell) for cell in (*used, self._get(row, c.account))):
            raise _Skip(IssueKind.TEMPLATE_ROWS_SKIPPED)
        if any("*" in cell for cell in used):
            raise _Skip(IssueKind.MASKED_ROWS)

    def _date(self, cell: str) -> date:
        try:
            return _parse_date(cell, self.fmt.date_format)
        except ValueError:
            pass
        swapped = _swapped_day_month(self.fmt.date_format)
        if swapped is not None:
            try:
                _parse_date(cell, swapped)
            except ValueError:
                pass
            else:
                raise _Skip(IssueKind.MONTH_DAY_DATES, cell.strip())
        raise _Skip(IssueKind.BAD_DATES, cell.strip())

    def _category(self, label: str) -> TxnType:
        category = self.types.get(norm(label))
        if category is None:
            self.tally.unknown_labels[label] += 1
            return TxnType.OTHER
        return category

    def _currency(self, cell: str) -> str | None:
        text = cell.strip()
        if text in ("", self.fmt.null_token):
            return None
        if text.upper() != "USD":
            raise _Skip(IssueKind.NON_USD_ROWS)
        return "USD"

    def _amounts(self, row: SectionRow, category: TxnType) -> _Amounts:
        cols, null = self.fmt.columns, self.fmt.null_token
        try:
            qty = parse_number(self._get(row, cols.quantity), null)
            price = parse_number(self._get(row, cols.price), null)
            gross = parse_number(self._get(row, cols.gross), null)
            commission = parse_number(self._get(row, cols.commission), null) or 0.0
            net = parse_number(self._get(row, cols.net), null)
        except _BadNumber as bad:
            raise _Skip(IssueKind.UNPARSEABLE_ROWS, bad.cell) from None
        mismatched = gross is not None and net is not None and abs(gross + commission - net) > _TOLERANCE
        if net is None:
            if gross is not None:
                net = gross + commission
            elif category in NON_CASH:
                net = 0.0
            else:
                raise _Skip(IssueKind.UNPARSEABLE_ROWS, self._get(row, cols.net) or null)
        gross = net - commission if gross is None else gross
        return _Amounts(qty, price, gross, commission, net, mismatched)

    def _share_change(
        self, category: TxnType, qty: float | None, symbol: str | None, row: SectionRow
    ) -> float:
        """Direction comes from the category, never from the sign in the file (mismatches are noted)."""
        if category not in POSITION_TYPES:
            return 0.0
        if qty is None or symbol is None:
            cols = self.fmt.columns
            missing = self._get(row, cols.quantity) if qty is None else self._get(row, cols.symbol)
            raise _Skip(IssueKind.UNPARSEABLE_ROWS, missing or self.fmt.null_token)
        if category in INCOMING:
            self.sign_mismatch += qty < 0
            return abs(qty)
        if category in OUTGOING:
            self.sign_mismatch += qty > 0
            return -abs(qty)
        return qty


def _issues(name: str, reader: _RowReader) -> list[Issue]:
    tally, w = reader.tally, Severity.WARNING
    issues: list[Issue] = []
    for kind in (IssueKind.BAD_DATES, IssueKind.UNPARSEABLE_ROWS):
        if tally.counts[kind]:
            context = {"file": name, "count": tally.counts[kind], "example": tally.examples[kind]}
            issues.append(Issue(kind, w, context))
    if tally.counts[IssueKind.MONTH_DAY_DATES]:
        example = tally.examples[IssueKind.MONTH_DAY_DATES]
        issues.append(Issue(IssueKind.MONTH_DAY_DATES, w, {"file": name, "example": example}))
    for kind in (IssueKind.MASKED_ROWS, IssueKind.NON_USD_ROWS):
        if tally.counts[kind]:
            issues.append(Issue(kind, w, {"file": name, "count": tally.counts[kind]}))
    for label, count in tally.unknown_labels.items():
        issues.append(Issue(IssueKind.UNKNOWN_TYPE, w, {"label": label, "count": count, "file": name}))
    if reader.net_mismatch:
        issues.append(Issue(IssueKind.NET_MISMATCH, w, {"file": name, "count": reader.net_mismatch}))
    if reader.sign_mismatch:
        context = {"file": name, "count": reader.sign_mismatch}
        issues.append(Issue(IssueKind.SIGN_MISMATCH, Severity.INFO, context))
    if reader.placeholders:
        issues.append(Issue(IssueKind.TEMPLATE_ROWS_SKIPPED, Severity.INFO, {"count": reader.placeholders}))
    return issues
