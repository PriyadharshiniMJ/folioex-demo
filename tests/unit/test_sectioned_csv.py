"""Section splitter and decoding (criteria I1, I4)."""

import pytest

from folioex.errors import FolioExError, IssueKind
from folioex.ingestion.sectioned_csv import decode, split_sections
from tests.builders import buy, deposit, make_csv


def test_sections_found_in_any_order_and_unknown_ignored() -> None:
    raw = make_csv(
        [deposit("01/01/2025", 100)],
        start_cash=0,
        end_cash=100,
        period="January 1, 2025 - December 31, 2025",
        sections=("Transaction History", "Notes", "Summary", "Statement"),
    )
    sections = split_sections(decode(raw, "a.csv"))
    assert {"Transaction History", "Summary", "Statement"} <= set(sections)
    txn = sections["Transaction History"]
    assert len(txn.rows) == 1
    assert txn.rows[0].values["Transaction Type"] == "Deposit"


def test_header_names_are_matched_loosely() -> None:
    text = (
        "Transaction History,Header,date , ACCOUNT,Transaction  Type\n"
        "Transaction History,Data,01/01/2025,U1,Deposit\n"
    )
    sec = split_sections(text)["Transaction History"]
    assert sec.get(sec.rows[0], "Date") == "01/01/2025"
    assert sec.get(sec.rows[0], "transaction type") == "Deposit"
    assert sec.get(sec.rows[0], "Missing") is None


def test_repeated_header_switches_columns() -> None:
    text = (
        "Transaction History,Header,Date,Transaction Type\n"
        "Transaction History,Data,01/01/2025,Deposit\n"
        "Transaction History,Header,Transaction Type,Date\n"
        "Transaction History,Data,Deposit,02/01/2025\n"
    )
    rows = split_sections(text)["Transaction History"].rows
    assert [r.values["Date"] for r in rows] == ["01/01/2025", "02/01/2025"]


def test_plain_table_without_section_columns_is_accepted() -> None:
    text = "Date,Account,Transaction Type,Symbol,Quantity,Net Amount\n01/01/2025,U1,Deposit,-,-,100\n"
    sections = split_sections(
        text, plain_section="Transaction History", plain_required=("Date", "Net Amount")
    )
    assert sections["Transaction History"].rows[0].values["Net Amount"] == "100"


def test_row_numbers_point_at_the_file_line() -> None:
    raw = make_csv(
        [deposit("01/01/2025", 1), buy("02/01/2025", "AAA", 1, 10)], sections=("Transaction History",)
    )
    rows = split_sections(decode(raw, "a.csv"))["Transaction History"].rows
    assert [r.line for r in rows] == [2, 3]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252", "utf-16"])
def test_common_encodings_decode(encoding: str) -> None:
    text = "Transaction History,Header,Date,Account\nTransaction History,Data,01/01/2025,Café\n"
    assert "Café" in decode(text.encode(encoding), "a.csv")


def test_undecodable_bytes_give_friendly_issue() -> None:
    with pytest.raises(FolioExError) as info:
        decode(b"\x81\x8d\x8f\x90\x9d" * 10, "weird.csv")
    assert info.value.issue.kind is IssueKind.FILE_UNREADABLE
    assert info.value.issue.context["file"] == "weird.csv"
