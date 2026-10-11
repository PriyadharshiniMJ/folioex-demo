"""Build small transaction-history CSV files in the template structure for tests.

Tickers here are made up on purpose: the app must work for any symbols.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

HEADER = [
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
Row = list[str]


def num(x: float) -> str:
    text = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def row(
    date: str,
    type_: str,
    symbol: str = "-",
    qty: str = "-",
    price: str = "-",
    currency: str = "-",
    gross: str = "-",
    commission: str = "-",
    net: str = "-",
    account: str = "U1",
) -> Row:
    return [date, account, type_, symbol, qty, price, currency, gross, commission, net]


def buy(date: str, symbol: str, qty: float, price: float, commission: float = -1.0) -> Row:
    gross = -qty * price
    return row(
        date, "Buy", symbol, num(qty), num(price), "USD", num(gross), num(commission), num(gross + commission)
    )


def sell(date: str, symbol: str, qty: float, price: float, commission: float = -1.0) -> Row:
    gross = qty * price
    return row(
        date,
        "Sell",
        symbol,
        num(-qty),
        num(price),
        "USD",
        num(gross),
        num(commission),
        num(gross + commission),
    )


def deposit(date: str, amount: float) -> Row:
    return row(date, "Deposit", gross=num(amount), net=num(amount))


def withdrawal(date: str, amount: float) -> Row:
    return row(date, "Withdrawal", gross=num(-amount), net=num(-amount))


def dividend(date: str, symbol: str, amount: float) -> Row:
    return row(date, "Dividend", symbol, gross=num(amount), net=num(amount))


def withholding(date: str, symbol: str, amount: float) -> Row:
    return row(date, "Foreign Tax Withholding", symbol, gross=num(-amount), net=num(-amount))


def fee(date: str, symbol: str, amount: float) -> Row:
    return row(date, "Other Fee", symbol, gross=num(-amount), net=num(-amount))


def make_csv(
    txns: Iterable[Row],
    *,
    start_cash: float | str | None = None,
    end_cash: float | str | None = None,
    period: str | None = None,
    sections: Sequence[str] = ("Statement", "Summary", "Transaction History"),
    header: Sequence[str] = HEADER,
    encoding: str = "utf-8",
) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    rows = list(txns)
    for section in sections:
        if section == "Statement":
            w.writerow(["Statement", "Header", "Field Name", "Field Value"])
            w.writerow(["Statement", "Data", "Title", "Transaction History"])
            if period:
                w.writerow(["Statement", "Data", "Period", period])
        elif section == "Summary" and (start_cash is not None or end_cash is not None):
            w.writerow(["Summary", "Header", "Field Name", "Field Value"])
            w.writerow(["Summary", "Data", "Base Currency", "USD"])
            if start_cash is not None:
                w.writerow(
                    [
                        "Summary",
                        "Data",
                        "Starting Cash",
                        start_cash if isinstance(start_cash, str) else num(start_cash),
                    ]
                )
            if end_cash is not None:
                w.writerow(
                    [
                        "Summary",
                        "Data",
                        "Ending Cash",
                        end_cash if isinstance(end_cash, str) else num(end_cash),
                    ]
                )
        elif section == "Transaction History":
            w.writerow(["Transaction History", "Header", *header])
            for r in rows:
                w.writerow(["Transaction History", "Data", *r])
        elif section not in ("Statement", "Summary"):
            w.writerow([section, "Header", "Anything"])
            w.writerow([section, "Data", "ignored"])
    return out.getvalue().encode(encoding)


def net_total(rows: Iterable[Row]) -> float:
    return sum(float(r[9]) for r in rows if r[9] not in ("-", ""))
