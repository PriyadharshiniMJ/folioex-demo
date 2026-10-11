"""Dividend income from the files only (never from market data, so nothing is counted twice).

Gross = dividend rows; tax = withholding rows; fees = fee rows tied to a stock (e.g. ADR custody fees).
Totals agree whether cut by month, by year or by stock.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from folioex.domain.models import Transaction, TxnType

_OTHER = "Other"


@dataclass(slots=True)
class Bucket:
    gross: float = 0.0
    tax: float = 0.0
    fees: float = 0.0

    @property
    def net(self) -> float:
        return self.gross + self.tax + self.fees


@dataclass(frozen=True, slots=True)
class MonthIncome:
    year: int
    month: int
    gross: float
    tax: float
    fees: float
    net: float


@dataclass(frozen=True, slots=True)
class YearIncome:
    year: int
    gross: float
    tax: float
    fees: float
    net: float
    yoy: float | None


@dataclass(frozen=True, slots=True)
class StockIncome:
    symbol: str
    payments: int
    gross: float
    tax: float
    fees: float
    net: float
    share: float


@dataclass(slots=True)
class Income:
    gross: float
    tax: float
    fees: float
    net: float
    by_month: list[MonthIncome] = field(default_factory=list)
    by_year: list[YearIncome] = field(default_factory=list)
    by_stock: list[StockIncome] = field(default_factory=list)


def _is_dividend_row(t: Transaction) -> bool:
    return t.type in (TxnType.DIVIDEND, TxnType.WITHHOLDING) or (
        t.type is TxnType.FEE and t.symbol is not None
    )


def _add(bucket: Bucket, t: Transaction) -> None:
    if t.type is TxnType.DIVIDEND:
        bucket.gross += t.net
    elif t.type is TxnType.WITHHOLDING:
        bucket.tax += t.net
    else:
        bucket.fees += t.net


def dividend_income(
    transactions: Sequence[Transaction], start: date, end: date, *, include_start: bool
) -> Income:
    total = Bucket()
    months: defaultdict[tuple[int, int], Bucket] = defaultdict(Bucket)
    years: defaultdict[int, Bucket] = defaultdict(Bucket)
    stocks: defaultdict[str, Bucket] = defaultdict(Bucket)
    payments: defaultdict[str, int] = defaultdict(int)
    for t in transactions:
        after_start = t.date >= start if include_start else t.date > start
        if not (after_start and t.date <= end and _is_dividend_row(t)):
            continue
        symbol = t.symbol or _OTHER
        for bucket in (total, months[(t.date.year, t.date.month)], years[t.date.year], stocks[symbol]):
            _add(bucket, t)
        if t.type is TxnType.DIVIDEND:
            payments[symbol] += 1
    by_year: list[YearIncome] = []
    previous: float | None = None
    for year in sorted(years):
        b = years[year]
        yoy = b.net / previous - 1 if previous else None
        by_year.append(YearIncome(year, b.gross, b.tax, b.fees, b.net, yoy))
        previous = b.net
    by_stock = [
        StockIncome(s, payments[s], b.gross, b.tax, b.fees, b.net, b.net / total.net if total.net else 0.0)
        for s, b in stocks.items()
    ]
    return Income(
        gross=total.gross,
        tax=total.tax,
        fees=total.fees,
        net=total.net,
        by_month=[MonthIncome(y, m, b.gross, b.tax, b.fees, b.net) for (y, m), b in sorted(months.items())],
        by_year=by_year,
        by_stock=sorted(by_stock, key=lambda s: -s.net),
    )
