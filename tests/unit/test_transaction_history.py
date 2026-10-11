"""One file → canonical transactions (criteria I1-I8). Format specifics come from config only."""

import re
from datetime import date
from pathlib import Path

import pytest

from folioex.config import FormatCfg, Settings
from folioex.domain.models import TxnType
from folioex.errors import FolioExError, IssueKind, Severity
from folioex.ingestion.transaction_history import parse_file
from tests.builders import HEADER, buy, deposit, dividend, make_csv, row, sell, withholding

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "structure_template.csv"
MASKED_HEADERS = {
    "Acco****": "Account",
    "Q****": "Quantity",
    "Price C****": "Price Currency",
    "Gross Amo****": "Gross Amount",
    "Net Amo****": "Net Amount",
}


def issue(result: object, kind: IssueKind) -> object:
    found = [i for i in result.issues if i.kind is kind]  # type: ignore[attr-defined]
    assert len(found) == 1, f"expected one {kind}, got {result.issues}"  # type: ignore[attr-defined]
    return found[0]


def kinds(result: object) -> set[IssueKind]:
    return {i.kind for i in result.issues}  # type: ignore[attr-defined]


@pytest.fixture
def fmt(settings: Settings) -> FormatCfg:
    return settings.format


# ---- the provided structure file (format test only) ----


def test_structure_file_as_provided_reports_its_masked_columns(fmt: FormatCfg) -> None:
    with pytest.raises(FolioExError) as info:
        parse_file(FIXTURE.name, FIXTURE.read_bytes(), fmt)
    assert info.value.issue.kind is IssueKind.MISSING_COLUMN
    assert info.value.issue.context["column"] == "Quantity"


def test_structure_file_with_real_header_names_parses(fmt: FormatCfg) -> None:
    text = FIXTURE.read_text(encoding="utf-8-sig")
    for masked, real in MASKED_HEADERS.items():
        text = text.replace(masked, real)
    text = re.sub(r"(?m)^S\*{4},", "Summary,", text)  # the section name only, not tickers like SS****
    result = parse_file(FIXTURE.name, text.encode(), fmt)
    by_type: dict[str, int] = {}
    for t in result.transactions:
        by_type[t.raw_type] = by_type.get(t.raw_type, 0) + 1
    # Rows with a masked value in any field FolioEx uses (incl. the 4 sells' masked currency) are skipped.
    assert by_type == {"Dividend": 78, "Foreign Tax Withholding": 76, "Other Fee": 12, "Deposit": 9}
    assert issue(result, IssueKind.MASKED_ROWS).context["count"] == 73  # type: ignore[attr-defined]
    assert result.rows_skipped == 73
    assert IssueKind.BAD_DATES not in kinds(result)
    first_dividend = next(t for t in result.transactions if t.raw_type == "Dividend")
    assert first_dividend.date == date(2026, 10, 8)  # "08/10/2026" is day/month/year
    assert first_dividend.quantity == 0 and first_dividend.price is None  # "-" means empty
    assert result.summary is not None and result.summary.starting_cash == pytest.approx(8000.0)
    assert result.summary.ending_cash == pytest.approx(2976.0)


# ---- generic rules on synthetic files ----


def test_basic_rows_map_to_canonical_transactions(fmt: FormatCfg) -> None:
    raw = make_csv(
        [deposit("02/01/2025", 1000), buy("03/01/2025", "AAA", 5, 100), sell("10/02/2025", "AAA", 2, 120)],
        start_cash=0,
        end_cash=1000 - 501 + 239,
        period="January 1, 2025 - December 31, 2025",
    )
    result = parse_file("t.csv", raw, fmt)
    dep, b, s = result.transactions
    assert (dep.type, dep.net, dep.symbol) == (TxnType.DEPOSIT, 1000, None)
    assert (b.type, b.symbol, b.quantity, b.price, b.commission, b.net) == (
        TxnType.BUY,
        "AAA",
        5,
        100,
        -1,
        -501,
    )
    assert (s.type, s.quantity, s.net) == (TxnType.SELL, -2, 239)
    assert result.period == (date(2025, 1, 1), date(2025, 12, 31))
    assert not result.issues


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("1,234.50", 1234.5), ("$1,234.50", 1234.5), ("(12.30)", -12.3), ("−7.5", -7.5), (" 3 ", 3.0)],
)
def test_number_formats(fmt: FormatCfg, cell: str, expected: float) -> None:
    raw = make_csv([row("02/01/2025", "Deposit", gross=cell, net=cell)], sections=("Transaction History",))
    assert parse_file("t.csv", raw, fmt).transactions[0].net == pytest.approx(expected)


def test_unreadable_dates_are_skipped_with_example(fmt: FormatCfg) -> None:
    raw = make_csv(
        [deposit("2026-13-01", 1), deposit("31/12/2026", 1), deposit("12/31/2026", 1)],
        sections=("Transaction History",),
    )
    result = parse_file("t.csv", raw, fmt)
    assert [t.date for t in result.transactions] == [date(2026, 12, 31)]
    bad = issue(result, IssueKind.BAD_DATES)
    assert bad.context["example"] == "2026-13-01" and bad.context["count"] == 1  # type: ignore[attr-defined]
    md = issue(result, IssueKind.MONTH_DAY_DATES)
    assert md.context["example"] == "12/31/2026"  # type: ignore[attr-defined]


def test_unknown_type_keeps_cash_but_never_moves_shares(fmt: FormatCfg) -> None:
    raw = make_csv(
        [row("02/01/2025", "Stock Award", "AAA", "10", "-", "-", "50", "-", "50")] * 2,
        sections=("Transaction History",),
    )
    result = parse_file("t.csv", raw, fmt)
    assert all(t.type is TxnType.OTHER and t.quantity == 0 and t.net == 50 for t in result.transactions)
    unknown = issue(result, IssueKind.UNKNOWN_TYPE)
    assert unknown.context == {"label": "Stock Award", "count": 2, "file": "t.csv"}  # type: ignore[attr-defined]


def test_direction_comes_from_the_type(fmt: FormatCfg) -> None:
    wrong_sign = row("02/01/2025", "Sell", "AAA", "3", "10", "USD", "30", "-1", "29")
    raw = make_csv([buy("01/01/2025", "AAA", 5, 10), wrong_sign], sections=("Transaction History",))
    result = parse_file("t.csv", raw, fmt)
    assert result.transactions[1].quantity == -3
    sign = issue(result, IssueKind.SIGN_MISMATCH)
    assert sign.severity is Severity.INFO  # type: ignore[attr-defined]


def test_non_usd_and_masked_rows_are_skipped(fmt: FormatCfg) -> None:
    eur = row("02/01/2025", "Buy", "AAA", "1", "10", "EUR", "-10", "-1", "-11")
    masked = row("02/01/2025", "B****", "AAA", "1", "10", "USD", "-10", "-1", "-11")
    masked_sym = row("02/01/2025", "Dividend", "****", gross="1", net="1")
    raw = make_csv([eur, masked, masked_sym, deposit("03/01/2025", 5)], sections=("Transaction History",))
    result = parse_file("t.csv", raw, fmt)
    assert len(result.transactions) == 1
    assert issue(result, IssueKind.NON_USD_ROWS).context["count"] == 1  # type: ignore[attr-defined]
    assert issue(result, IssueKind.MASKED_ROWS).context["count"] == 2  # type: ignore[attr-defined]
    assert result.rows_skipped == 3


def test_unreadable_numbers_skip_the_row(fmt: FormatCfg) -> None:
    raw = make_csv(
        [row("02/01/2025", "Deposit", gross="abc", net="abc"), deposit("03/01/2025", 5)],
        sections=("Transaction History",),
    )
    result = parse_file("t.csv", raw, fmt)
    assert len(result.transactions) == 1
    assert issue(result, IssueKind.UNPARSEABLE_ROWS).context["example"] == "abc"  # type: ignore[attr-defined]


def test_template_placeholder_rows_are_skipped_quietly(fmt: FormatCfg) -> None:
    placeholder = row(
        "<DATE DD/MM/YYYY>",
        "Buy",
        "<TICKER SYMBOL>",
        "<DECIMAL +>",
        "<DECIMAL +>",
        "USD",
        "<DECIMAL −>",
        "<DECIMAL −>",
        "<DECIMAL −>",
    )
    raw = make_csv([placeholder, deposit("03/01/2025", 5)], sections=("Transaction History",))
    result = parse_file("t.csv", raw, fmt)
    assert len(result.transactions) == 1
    skipped = issue(result, IssueKind.TEMPLATE_ROWS_SKIPPED)
    assert skipped.severity is Severity.INFO  # type: ignore[attr-defined]


def test_missing_required_column_is_blocking(fmt: FormatCfg) -> None:
    header = [h for h in HEADER if h != "Net Amount"]
    raw = make_csv([deposit("01/01/2025", 1)[:-1]], header=header, sections=("Transaction History",))
    with pytest.raises(FolioExError) as info:
        parse_file("2024.csv", raw, fmt)
    assert info.value.issue.context == {"file": "2024.csv", "column": "Net Amount"}


def test_file_without_transactions_section_is_blocking(fmt: FormatCfg) -> None:
    with pytest.raises(FolioExError) as info:
        parse_file("x.csv", b"hello,world\n1,2\n", fmt)
    assert info.value.issue.kind is IssueKind.NO_TRANSACTIONS_SECTION


def test_commission_and_net_defaults(fmt: FormatCfg) -> None:
    no_net = row("02/01/2025", "Buy", "AAA", "2", "10", "USD", "-20", "-1", "-")
    no_comm = row("02/01/2025", "Buy", "BBB", "1", "10", "USD", "-10", "-", "-10")
    split = row("03/01/2025", "Stock Split", "AAA", "2", "-", "-", "-", "-", "-")
    raw = make_csv([no_net, no_comm, split], sections=("Transaction History",))
    a, b, s = parse_file("t.csv", raw, fmt).transactions
    assert (a.net, b.commission, s.type, s.quantity, s.net) == (-21, 0, TxnType.SPLIT, 2, 0)


def test_net_that_does_not_add_up_is_flagged(fmt: FormatCfg) -> None:
    raw = make_csv(
        [row("02/01/2025", "Buy", "AAA", "2", "10", "USD", "-20", "-1", "-25")],
        sections=("Transaction History",),
    )
    result = parse_file("t.csv", raw, fmt)
    assert result.transactions[0].net == -25  # net as written
    assert IssueKind.NET_MISMATCH in kinds(result)


def test_type_labels_match_ignoring_case_and_spacing(fmt: FormatCfg) -> None:
    raw = make_csv([withholding("02/01/2025", "AAA", 1)], sections=("Transaction History",))
    raw = raw.replace(b"Foreign Tax Withholding", b"foreign tax  WITHHOLDING ")
    assert parse_file("t.csv", raw, fmt).transactions[0].type is TxnType.WITHHOLDING


def test_symbols_are_normalized(fmt: FormatCfg) -> None:
    raw = make_csv(
        [dividend("02/01/2025", "BRK B", 1), dividend("02/01/2025", "brk.b", 1)],
        sections=("Transaction History",),
    )
    assert {t.symbol for t in parse_file("t.csv", raw, fmt).transactions} == {"BRK-B"}


def test_everything_is_config_driven(settings: Settings) -> None:
    fmt = settings.format.model_copy(
        update={
            "date_format": "%Y-%m-%d",
            "columns": settings.format.columns.model_copy(update={"net": "Amount", "type": "Kind"}),
            "types": {**settings.format.types, "Einzahlung": TxnType.DEPOSIT},
        }
    )
    header = [{"Net Amount": "Amount", "Transaction Type": "Kind"}.get(h, h) for h in HEADER]
    raw = make_csv(
        [row("2025-01-02", "Einzahlung", gross="10", net="10")],
        header=header,
        sections=("Transaction History",),
    )
    t = parse_file("t.csv", raw, fmt).transactions[0]
    assert (t.date, t.type, t.net) == (date(2025, 1, 2), TxnType.DEPOSIT, 10)


def test_rows_keep_source_and_line(fmt: FormatCfg) -> None:
    raw = make_csv([deposit("02/01/2025", 1)], sections=("Transaction History",))
    t = parse_file("mine.csv", raw, fmt).transactions[0]
    assert (t.source_file, t.line) == ("mine.csv", 2)
