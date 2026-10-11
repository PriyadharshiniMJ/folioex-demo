"""Multi-file ingestion: limits, merge/dedupe, reconciliation, gaps, ordering (criteria I9, I10, E4)."""

import random
from datetime import date

import pytest

from folioex.config import Settings
from folioex.domain.models import TxnType
from folioex.errors import IssueKind, Severity
from folioex.ingestion.pipeline import IngestResult, Upload, ingest
from tests.builders import buy, deposit, dividend, make_csv, net_total, sell

MB = 1024 * 1024


def kinds(result: IngestResult) -> list[IssueKind]:
    return [i.kind for i in result.issues]


def one(result: IngestResult, kind: IssueKind) -> dict[str, object]:
    found = [i for i in result.all_issues() if i.kind is kind]
    assert len(found) == 1, f"expected one {kind}, got {[i.kind for i in result.all_issues()]}"
    return dict(found[0].context)


def year(y: int, rows: list[list[str]], start: float, **kw: object) -> bytes:
    return make_csv(
        rows,
        start_cash=start,
        end_cash=start + net_total(rows),
        period=f"January 1, {y} - December 31, {y}",
        **kw,  # type: ignore[arg-type]
    )


Y2024 = [deposit("02/01/2024", 2000), buy("03/01/2024", "AAA", 10, 100), dividend("15/03/2024", "AAA", 5)]
Y2025 = [deposit("02/01/2025", 1000), sell("10/02/2025", "AAA", 4, 120), buy("10/02/2025", "BBB", 3, 50)]


def test_complete_history_is_clean(settings: Settings) -> None:
    end_2024 = net_total(Y2024)  # started 2024 with no cash
    result = ingest(
        [Upload("2024.csv", year(2024, Y2024, 0)), Upload("2025.csv", year(2025, Y2025, end_2024))], settings
    )
    assert result.issues == []
    assert [f.status for f in result.files] == ["ok", "ok"]
    assert [f.cash_check for f in result.files] == ["ok", "ok"]
    assert len(result.transactions) == 6
    assert result.tickers == ["AAA", "BBB"]
    assert result.date_range == (date(2024, 1, 2), date(2025, 2, 10))


def test_dedupe_keeps_max_count_per_file(settings: Settings) -> None:
    x, y, z = deposit("02/01/2025", 10), deposit("03/01/2025", 20), deposit("04/01/2025", 30)
    a = make_csv([x, x, y], sections=("Transaction History",))
    b = make_csv([x, y, z], sections=("Transaction History",))
    result = ingest([Upload("a.csv", a), Upload("b.csv", b)], settings)
    assert sorted(t.net for t in result.transactions) == [10, 10, 20, 30]
    assert one(result, IssueKind.DUPLICATES_IGNORED)["count"] == 2


def test_same_file_twice_adds_nothing_and_partial_fills_survive(settings: Settings) -> None:
    fills = [deposit("01/01/2025", 1000), sell("04/08/2025", "AAA", 1, 50), sell("04/08/2025", "AAA", 1, 50)]
    raw = make_csv([buy("02/01/2025", "AAA", 2, 10), *fills], sections=("Transaction History",))
    single = ingest([Upload("a.csv", raw)], settings)
    double = ingest([Upload("a.csv", raw), Upload("a copy.csv", raw)], settings)
    assert len(single.transactions) == len(double.transactions) == 4
    assert one(double, IssueKind.DUPLICATES_IGNORED)["count"] == 4


def test_cash_mismatch_reports_the_amount(settings: Settings) -> None:
    rows = [deposit("02/01/2025", 100)]
    raw = make_csv(rows, start_cash=0, end_cash=87.70)
    result = ingest([Upload("2025.csv", raw)], settings)
    assert result.files[0].cash_check == "off"
    assert one(result, IssueKind.CASH_MISMATCH) == {"file": "2025.csv", "amount": "$12.30"}
    assert result.files[0].status == "warn"


def test_cash_continuity_between_files(settings: Settings) -> None:
    a = year(2024, [deposit("02/01/2024", 100)], 0)
    b = year(2025, [deposit("02/01/2025", 100)], 50)  # 2024 ended with 100
    result = ingest([Upload("2025.csv", b), Upload("2024.csv", a)], settings)
    assert one(result, IssueKind.CASH_CONTINUITY) == {"previous": "2024.csv", "file": "2025.csv"}


def test_starting_cash_becomes_an_opening_deposit(settings: Settings) -> None:
    raw = year(2025, [buy("03/01/2025", "AAA", 1, 100)], 500)
    result = ingest([Upload("2025.csv", raw)], settings)
    first = result.transactions[0]
    assert (first.date, first.type, first.net, first.raw_type) == (
        date(2025, 1, 1),
        TxnType.DEPOSIT,
        500,
        "Opening cash",
    )
    assert result.issues == []


def test_selling_shares_never_bought_is_a_history_gap(settings: Settings) -> None:
    raw = make_csv(
        [sell("12/01/2026", "ZZZ", 18, 60), buy("13/01/2026", "AAA", 1, 1), sell("14/01/2026", "AAA", 3, 1)],
        sections=("Transaction History",),
    )
    result = ingest([Upload("2026.csv", raw)], settings)
    gaps = [dict(i.context) for i in result.issues if i.kind is IssueKind.HISTORY_GAP]
    assert gaps == [
        {"symbol": "ZZZ", "shares": "18", "date": "12/01/2026"},
        {"symbol": "AAA", "shares": "2", "date": "14/01/2026"},
    ]


def test_dividend_without_shares_is_flagged_once_per_symbol(settings: Settings) -> None:
    raw = make_csv(
        [dividend("05/01/2026", "KKK", 5), dividend("05/04/2026", "KKK", 5)],
        sections=("Transaction History",),
    )
    result = ingest([Upload("2026.csv", raw)], settings)
    assert one(result, IssueKind.DIVIDEND_WITHOUT_SHARES) == {"symbol": "KKK", "date": "05/01/2026"}


def test_gap_between_statement_periods(settings: Settings) -> None:
    a = year(2023, [deposit("02/01/2023", 1)], 0)
    c = year(2025, [deposit("02/01/2025", 1)], 1)
    result = ingest([Upload("2023.csv", a), Upload("2025.csv", c)], settings)
    assert one(result, IssueKind.COVERAGE_GAP) == {"start": "01/01/2024", "end": "31/12/2024"}


def test_rows_are_ordered_by_date_then_type_regardless_of_input_order(settings: Settings) -> None:
    rows = [
        buy("03/01/2025", "AAA", 1, 10),
        deposit("03/01/2025", 100),
        dividend("02/01/2025", "AAA", 1),
        sell("03/01/2025", "AAA", 1, 11),
        deposit("01/01/2025", 5),
    ]
    expected = None
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        result = ingest([Upload("a.csv", make_csv(shuffled, sections=("Transaction History",)))], settings)
        order = [(t.date, t.type, t.net) for t in result.transactions]
        expected = expected or order
        assert order == expected
    assert [t for _, t, _ in expected][2:] == [TxnType.DEPOSIT, TxnType.BUY, TxnType.SELL]


def test_limits_and_bad_files_never_stop_good_files(settings: Settings) -> None:
    good = make_csv([deposit("02/01/2025", 10)], sections=("Transaction History",))
    uploads = [
        Upload("report.pdf", b"%PDF-1.4"),
        Upload("broken.csv", b"\x81\x8d\x8f\x90\x9d" * 5),
        Upload("big.csv", b"x" * (6 * MB)),
        Upload("good.csv", good),
    ] + [Upload(f"extra{i}.csv", good) for i in range(18)]  # 22 files in total
    result = ingest(uploads, settings)
    assert one(result, IssueKind.TOO_MANY_FILES) == {"limit": 20}
    statuses = {f.name: f.status for f in result.files}
    assert (
        statuses["report.pdf"] == "bad" and statuses["broken.csv"] == "bad" and statuses["big.csv"] == "bad"
    )
    assert statuses["good.csv"] == "ok"
    assert "extra17.csv" not in statuses  # beyond the first 20
    assert sum(t.net for t in result.transactions) == 10  # duplicates of good.csv collapse
    by_file = {f.name: [i.kind for i in f.issues] for f in result.files}
    assert by_file["report.pdf"] == [IssueKind.FILE_NOT_CSV]
    assert by_file["broken.csv"] == [IssueKind.FILE_UNREADABLE]
    assert by_file["big.csv"] == [IssueKind.FILE_TOO_LARGE]


def test_total_size_limit(settings: Settings) -> None:
    chunk = b"x" * (4 * MB)
    uploads = [Upload(f"f{i}.csv", chunk) for i in range(7)]  # 28 MB in total
    result = ingest(uploads, settings)
    assert [f.name for f in result.files if any(i.kind is IssueKind.TOTAL_TOO_LARGE for i in f.issues)] == [
        "f6.csv"
    ]


def test_untouched_template_gives_no_transactions_and_one_info(settings: Settings) -> None:
    from folioex.ingestion.template import template_csv

    result = ingest([Upload("FolioEx_template.csv", template_csv(settings.format).encode())], settings)
    assert result.transactions == []
    template_issues = [i for f in result.files for i in f.issues if i.kind is IssueKind.TEMPLATE_ROWS_SKIPPED]
    assert len(template_issues) == 1
    assert template_issues[0].severity is Severity.INFO


def test_issues_are_sorted_most_serious_first(settings: Settings) -> None:
    raw = make_csv([sell("12/01/2026", "ZZZ", 1, 1), deposit("bad", 1)], sections=("Transaction History",))
    result = ingest([Upload("x.csv", raw), Upload("x2.csv", raw)], settings)
    severities = [i.severity for i in result.all_issues()]
    order = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
    assert severities == sorted(severities, key=order.__getitem__)


@pytest.mark.parametrize("name", ["data.CSV", "data.csv"])
def test_extension_check_is_case_insensitive(settings: Settings, name: str) -> None:
    raw = make_csv([deposit("02/01/2025", 1)], sections=("Transaction History",))
    assert ingest([Upload(name, raw)], settings).files[0].status == "ok"
