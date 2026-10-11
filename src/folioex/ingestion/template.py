"""The downloadable template and its column guide, generated from config so they always match the parser.

The template holds placeholders only (never real data or company names). Each cell says what it expects.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from folioex.config import FormatCfg
from folioex.domain.models import TxnType

TICKER = "<TICKER SYMBOL>"
TEXT = "<TEXT>"
POS = "<DECIMAL +>"
NEG = "<DECIMAL −>"
ANY = "<DECIMAL ±>"
USD = "USD"

# One placeholder row per category, in the order an investor usually thinks about them.
TEMPLATE_CATEGORIES: tuple[TxnType, ...] = (
    TxnType.DEPOSIT,
    TxnType.BUY,
    TxnType.SELL,
    TxnType.DIVIDEND,
    TxnType.WITHHOLDING,
    TxnType.FEE,
    TxnType.INTEREST,
    TxnType.WITHDRAWAL,
    TxnType.TRANSFER_IN,
    TxnType.TRANSFER_OUT,
    TxnType.SPLIT,
)

# (symbol, quantity, price, currency, gross, commission, net); None = the null token.
_SHAPES: dict[TxnType, tuple[str | None, ...]] = {
    TxnType.DEPOSIT: (None, None, None, None, POS, None, POS),
    TxnType.BUY: (TICKER, POS, POS, USD, NEG, NEG, NEG),
    TxnType.SELL: (TICKER, NEG, POS, USD, POS, NEG, POS),
    TxnType.DIVIDEND: (TICKER, None, None, None, POS, None, POS),
    TxnType.WITHHOLDING: (TICKER, None, None, None, NEG, None, NEG),
    TxnType.FEE: (TICKER, None, None, None, NEG, None, NEG),
    TxnType.INTEREST: (None, None, None, None, POS, None, POS),
    TxnType.WITHDRAWAL: (None, None, None, None, NEG, None, NEG),
    TxnType.TRANSFER_IN: (TICKER, POS, POS, USD, None, None, None),
    TxnType.TRANSFER_OUT: (TICKER, NEG, None, None, None, None, None),
    TxnType.SPLIT: (TICKER, ANY, None, None, None, None, None),
}

_DATE_PARTS = {"%d": "DD", "%m": "MM", "%Y": "YYYY", "%y": "YY", "%b": "MON", "%B": "MONTH"}


def date_pattern(date_format: str) -> str:
    text = date_format
    for code, word in _DATE_PARTS.items():
        text = text.replace(code, word)
    return text


def label_for(fmt: FormatCfg, category: TxnType) -> str:
    """The first file label that maps to a category (config order), e.g. INTEREST → "Credit Interest"."""
    return next(label for label, cat in fmt.types.items() if cat is category)


def template_csv(fmt: FormatCfg) -> str:
    cols = fmt.columns
    header = [
        cols.date,
        cols.account,
        cols.type,
        cols.symbol,
        cols.quantity,
        cols.price,
        cols.currency,
        cols.gross,
        cols.commission,
        cols.net,
    ]
    date_cell = f"<DATE {date_pattern(fmt.date_format)}>"
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([fmt.statement_section, "Header", "Field Name", "Field Value"])
    w.writerow([fmt.statement_section, "Data", "Title", fmt.section])
    w.writerow([fmt.statement_section, "Data", "Period", "<TEXT: period these rows cover>"])
    w.writerow([fmt.summary_section, "Header", "Field Name", "Field Value"])
    w.writerow([fmt.summary_section, "Data", "Base Currency", "USD"])
    w.writerow([fmt.summary_section, "Data", "Starting Cash", "<DECIMAL: cash at period start>"])
    w.writerow([fmt.summary_section, "Data", "Ending Cash", "<DECIMAL: cash at period end>"])
    w.writerow([fmt.section, "Header", *header])
    for category in TEMPLATE_CATEGORIES:
        shape = [fmt.null_token if cell is None else cell for cell in _SHAPES[category]]
        w.writerow([fmt.section, "Data", date_cell, TEXT, label_for(fmt, category), *shape])
    return out.getvalue()


@dataclass(frozen=True, slots=True)
class GuideRow:
    column: str
    data_type: str
    format: str
    placeholder: str
    required: str


def column_guide(fmt: FormatCfg) -> list[GuideRow]:
    cols = fmt.columns
    labels = ", ".join(fmt.types)
    pattern = date_pattern(fmt.date_format)
    return [
        GuideRow(cols.date, "Date", pattern, f"<DATE {pattern}>", "Yes"),
        GuideRow(cols.account, "Text", "Any account name or number", TEXT, "No"),
        GuideRow(cols.type, "Text (fixed list)", labels, label_for(fmt, TxnType.BUY), "Yes"),
        GuideRow(
            cols.symbol,
            "Ticker symbol",
            f"Upper-case ticker as shown by your broker; {fmt.null_token} when the row isn't about a stock",
            TICKER,
            "For stock rows",
        ),
        GuideRow(
            cols.quantity,
            "Decimal",
            f"Shares: + for buys and transfers in, − for sells and transfers out; {fmt.null_token} otherwise",
            POS,
            "For buys and sells",
        ),
        GuideRow(cols.price, "Decimal", "Price per share in USD", POS, "For buys and sells"),
        GuideRow(cols.currency, "Text", "USD", USD, "For buys and sells"),
        GuideRow(cols.gross, "Decimal", "Quantity × price before fees; + money in, − money out", NEG, "Yes"),
        GuideRow(
            cols.commission,
            "Decimal",
            f"Fee charged, as a negative number; {fmt.null_token} if none",
            NEG,
            "No",
        ),
        GuideRow(cols.net, "Decimal", "Gross + commission: money in (+) or out (−)", NEG, "Yes"),
    ]
