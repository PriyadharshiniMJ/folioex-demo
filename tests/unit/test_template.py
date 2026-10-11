"""Downloadable template: placeholders only, config-driven, and it parses once filled in (criterion I11)."""

import csv
import io
import re
from pathlib import Path

from folioex.config import Settings
from folioex.domain.models import TxnType
from folioex.ingestion.pipeline import Upload, ingest
from folioex.ingestion.template import TEMPLATE_CATEGORIES, column_guide, template_csv

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "structure_template.csv"


def data_rows(text: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(text)) if r[:2] == ["Transaction History", "Data"]]


def signed(placeholder: str, size: float) -> float:
    return -size if "−" in placeholder else size


def fill(text: str) -> str:
    """Replace every placeholder the way an investor would: consistent amounts, cash that adds up."""
    rows = list(csv.reader(io.StringIO(text)))
    header = next(r[2:] for r in rows if r[:2] == ["Transaction History", "Header"])
    total = 0.0
    for r in rows:
        if r[:2] != ["Transaction History", "Data"]:
            continue
        cell = dict(zip(header, r[2:], strict=True))
        gross = signed(cell["Gross Amount"], 20) if cell["Gross Amount"].startswith("<") else 0.0
        commission = -1.0 if cell["Commission"].startswith("<") else 0.0
        net = gross + commission if cell["Net Amount"].startswith("<") else 0.0
        values = {
            "Date": "15/01/2026",
            "Account": "U1",
            "Symbol": "AAA",
            "Quantity": "2" if "±" in cell["Quantity"] else str(signed(cell["Quantity"], 10)),
            "Price": "10",
            "Gross Amount": str(gross),
            "Commission": str(commission),
            "Net Amount": str(net),
        }
        r[2:] = [values[h] if cell[h].startswith("<") else cell[h] for h in header]
        total += net
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    filled = out.getvalue().replace("<DECIMAL: cash at period start>", "0")
    filled = filled.replace("<DECIMAL: cash at period end>", str(total))
    return re.sub(r"<TEXT: [^>]*>", "January 1, 2026 - December 31, 2026", filled)


def test_template_uses_ticker_placeholder_and_no_real_symbols(settings: Settings) -> None:
    text = template_csv(settings.format)
    assert "<TICKER SYMBOL>" in text
    fixture_rows = csv.reader(io.StringIO(FIXTURE.read_text(encoding="utf-8-sig")))
    fixture_symbols = {r[5] for r in fixture_rows if len(r) > 5}
    real = (set(settings.sample.tickers) | set(settings.benchmark.choices) | fixture_symbols) - {
        "-",
        "Symbol",
        "",
    }
    cells = {c for r in csv.reader(io.StringIO(text)) for c in r}
    assert not (cells & real)


def test_data_rows_are_placeholders_or_fixed_values_only(settings: Settings) -> None:
    rows = data_rows(template_csv(settings.format))
    assert rows
    for r in rows:
        assert r[2] == "<DATE DD/MM/YYYY>"
        assert not re.search(r"\d", ",".join(r[2:])), r


def test_one_row_per_category(settings: Settings) -> None:
    rows = data_rows(template_csv(settings.format))
    labels = [r[4] for r in rows]
    assert [settings.format.types[label] for label in labels] == list(TEMPLATE_CATEGORIES)
    assert TxnType.OTHER not in TEMPLATE_CATEGORIES
    assert set(TEMPLATE_CATEGORIES) == set(TxnType) - {TxnType.OTHER}


def test_filled_in_template_parses_cleanly(settings: Settings) -> None:
    text = fill(template_csv(settings.format))
    assert "<" not in text
    result = ingest([Upload("filled.csv", text.encode())], settings)
    assert len(result.transactions) == len(TEMPLATE_CATEGORIES)
    file_issues = [i.kind for f in result.files for i in f.issues]
    assert file_issues == []


def test_column_guide_lists_every_column_with_a_data_type(settings: Settings) -> None:
    guide = column_guide(settings.format)
    assert [g.column for g in guide] == [
        "Date",
        "Account",
        "Transaction Type",
        "Symbol",
        "Quantity",
        "Price",
        "Price Currency",
        "Gross Amount",
        "Commission",
        "Net Amount",
    ]
    assert all(g.data_type and g.format and g.placeholder and g.required for g in guide)
    symbol = next(g for g in guide if g.column == "Symbol")
    assert symbol.placeholder == "<TICKER SYMBOL>"
    types = next(g for g in guide if g.column == "Transaction Type")
    assert "Buy" in types.format and "Foreign Tax Withholding" in types.format


def test_template_follows_config(settings: Settings) -> None:
    fmt = settings.format.model_copy(
        update={
            "date_format": "%Y-%m-%d",
            "columns": settings.format.columns.model_copy(update={"symbol": "Ticker"}),
        }
    )
    text = template_csv(fmt)
    assert "<DATE YYYY-MM-DD>" in text
    assert ",Ticker," in text
