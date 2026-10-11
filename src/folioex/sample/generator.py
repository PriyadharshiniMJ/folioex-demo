"""Build a realistic, self-consistent transaction history from market data. Pure: prices in, CSV text out.

The investor in the story deposits every month, buys a couple of the configured tickers, now and then sells
part of a position, collects real dividends, earns a little interest, pays a small yearly fee and makes the
odd withdrawal. Prices come from ``History``, whose closes are split-adjusted (as yfinance provides them), so
trades are converted back to the prices people actually paid and each split is written as its own row.
"""

from __future__ import annotations

import bisect
import csv
import io
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from folioex.config import FormatCfg, SampleCfg
from folioex.domain.models import TxnType
from folioex.ingestion.template import label_for

INITIAL_DEPOSIT = 10_000.0
CASH_BUFFER = 150.0
COMMISSION = 1.0
TICKERS_PER_MONTH = 2
SELL_MONTHS = (3, 6, 9, 12)
SELL_ON_TRADING_DAY = 3
SELL_CHANCE = 0.5
SELL_SHARE = 0.25
PAY_LAG = timedelta(days=14)
INTEREST_RATE = 0.01
ANNUAL_FEE = 5.0
WITHDRAWAL_SHARE = 0.75  # of a monthly deposit, in July every other year
ACCOUNT = "DEMO"


@dataclass(frozen=True, slots=True)
class History:
    """Market data for the generator. ``close`` is aligned to ``dates`` and split-adjusted."""

    dates: Sequence[date]
    close: Mapping[str, Sequence[float | None]]
    dividends: Mapping[str, Sequence[tuple[date, float]]]  # ex-date, split-adjusted amount per share
    splits: Mapping[str, Sequence[tuple[date, float]]]  # date, ratio (3.0 = 3-for-1)


@dataclass(frozen=True, slots=True)
class _Row:
    date: date
    type: TxnType
    symbol: str | None = None
    quantity: float | None = None
    price: float | None = None
    gross: float | None = None
    commission: float | None = None
    net: float | None = None


class _Story:
    def __init__(self, history: History, cfg: SampleCfg, days: list[date]) -> None:
        self.history = history
        self.cfg = cfg
        self.days = days
        self.rng = random.Random(cfg.seed)
        self.tickers = [t for t in cfg.tickers if t in history.close]
        self.index = {d: i for i, d in enumerate(history.dates)}
        self.splits = {t: dict(history.splits.get(t, ())) for t in self.tickers}
        self.dividends = {(t, d): amount for t in self.tickers for d, amount in history.dividends.get(t, ())}
        self.rotation = self.tickers[:]
        self.rng.shuffle(self.rotation)
        self.turn = 0
        self.cash = 0.0
        self.shares: defaultdict[str, float] = defaultdict(float)
        self.payouts: defaultdict[date, list[tuple[str, float]]] = defaultdict(list)
        self.rows: list[_Row] = []

    # ---- prices ----

    def factor_after(self, ticker: str, day: date) -> float:
        factor = 1.0
        for split_day, ratio in self.splits[ticker].items():
            if split_day > day:
                factor *= ratio
        return factor

    def price(self, ticker: str, day: date) -> float | None:
        i = self.index.get(day)
        close = None if i is None else self.history.close[ticker][i]
        if close is None or close <= 0:
            return None
        return round(close * self.factor_after(ticker, day), 2)

    # ---- bookkeeping ----

    def book(self, row: _Row) -> None:
        self.rows.append(row)
        self.cash = round(self.cash + (row.net or 0.0), 2)

    def trading_day_on_or_after(self, day: date) -> date | None:
        i = bisect.bisect_left(self.days, day)
        return self.days[i] if i < len(self.days) else None

    # ---- one day of the story ----

    def run(self) -> list[_Row]:
        month_days: defaultdict[tuple[int, int], list[date]] = defaultdict(list)
        for d in self.days:
            month_days[(d.year, d.month)].append(d)
        for d in self.days:
            in_month = month_days[(d.year, d.month)]
            self.corporate_actions(d)
            self.pay_dividends(d)
            if d == in_month[0]:
                self.month_start(d)
            if (
                d.month in SELL_MONTHS
                and len(in_month) >= SELL_ON_TRADING_DAY
                and d == in_month[SELL_ON_TRADING_DAY - 1]
            ):
                self.maybe_sell(d)
            if d == in_month[-1]:
                self.interest(d)
        return self.rows

    def corporate_actions(self, d: date) -> None:
        for t in self.tickers:
            ratio = self.splits[t].get(d)
            if ratio and self.shares[t] > 0:
                extra = self.shares[t] * (ratio - 1)
                self.shares[t] += extra
                self.book(_Row(d, TxnType.SPLIT, t, quantity=extra))
            amount = self.dividends.get((t, d))
            if amount and self.shares[t] > 0:
                pay_day = self.trading_day_on_or_after(d + PAY_LAG)
                if pay_day is not None:
                    total = round(self.shares[t] * amount * self.factor_after(t, d), 2)
                    if total >= 0.01:
                        self.payouts[pay_day].append((t, total))

    def pay_dividends(self, d: date) -> None:
        for ticker, amount in self.payouts.pop(d, []):
            self.book(_Row(d, TxnType.DIVIDEND, ticker, gross=amount, net=amount))
            tax = round(amount * self.cfg.withholding_rate, 2)
            if tax > 0:
                self.book(_Row(d, TxnType.WITHHOLDING, ticker, gross=-tax, net=-tax))

    def month_start(self, d: date) -> None:
        first_day = d == self.days[0]
        deposit = self.cfg.monthly_deposit + (INITIAL_DEPOSIT if first_day else 0.0)
        self.book(_Row(d, TxnType.DEPOSIT, gross=deposit, net=deposit))
        if d.month == 1 and not first_day:
            self.book(_Row(d, TxnType.FEE, gross=-ANNUAL_FEE, net=-ANNUAL_FEE))
        if d.month == 7 and (d.year - self.cfg.years[0]) % 2 == 1:
            amount = round(self.cfg.monthly_deposit * WITHDRAWAL_SHARE, 2)
            self.book(_Row(d, TxnType.WITHDRAWAL, gross=-amount, net=-amount))
        self.buy(d)

    def buy(self, d: date) -> None:
        picks = []
        for _ in range(TICKERS_PER_MONTH):
            picks.append(self.rotation[self.turn % len(self.rotation)])
            self.turn += 1
        budget = self.cash - CASH_BUFFER
        if budget <= 0:
            return
        each = budget / len(picks)
        for t in picks:
            price = self.price(t, d)
            if price is None:
                continue
            qty = int((each - COMMISSION) // price)
            if qty < 1:
                continue
            gross = -round(qty * price, 2)
            self.shares[t] += qty
            self.book(_Row(d, TxnType.BUY, t, qty, price, gross, -COMMISSION, round(gross - COMMISSION, 2)))

    def maybe_sell(self, d: date) -> None:
        if self.rng.random() >= SELL_CHANCE:
            return
        held = [t for t in self.tickers if self.shares[t] >= 4 and self.price(t, d) is not None]
        if not held:
            return
        t = self.rng.choice(held)
        price = self.price(t, d)
        assert price is not None
        qty = max(1, int(self.shares[t] * SELL_SHARE))
        gross = round(qty * price, 2)
        self.shares[t] -= qty
        self.book(_Row(d, TxnType.SELL, t, -qty, price, gross, -COMMISSION, round(gross - COMMISSION, 2)))

    def interest(self, d: date) -> None:
        amount = round(self.cash * INTEREST_RATE / 12, 2)
        if amount >= 0.01:
            self.book(_Row(d, TxnType.INTEREST, gross=amount, net=amount))


def _num(value: float | None, null: str, *, money: bool = True) -> str:
    if value is None:
        return null
    if not money and float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _period(year: int, last: date) -> str:
    end = last if last.year == year else date(year, 12, 31)
    return f"January 1, {year} - {end:%B} {end.day}, {end.year}"


def _write_year(
    year: int, rows: list[_Row], *, start_cash: float, end_cash: float, last: date, fmt: FormatCfg
) -> str:
    cols, null = fmt.columns, fmt.null_token
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow([fmt.statement_section, "Header", "Field Name", "Field Value"])
    w.writerow([fmt.statement_section, "Data", "Title", fmt.section])
    w.writerow([fmt.statement_section, "Data", "Period", _period(year, last)])
    w.writerow([fmt.summary_section, "Header", "Field Name", "Field Value"])
    w.writerow([fmt.summary_section, "Data", "Base Currency", "USD"])
    w.writerow([fmt.summary_section, "Data", "Starting Cash", f"{start_cash:.2f}"])
    w.writerow([fmt.summary_section, "Data", "Ending Cash", f"{end_cash:.2f}"])
    w.writerow(
        [
            fmt.section,
            "Header",
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
    )
    for r in reversed(rows):  # newest first, like a broker export
        trade = r.price is not None
        w.writerow(
            [
                fmt.section,
                "Data",
                r.date.strftime(fmt.date_format),
                ACCOUNT,
                label_for(fmt, r.type),
                r.symbol or null,
                _num(r.quantity, null, money=False),
                _num(r.price, null),
                "USD" if trade else null,
                _num(r.gross, null),
                _num(r.commission, null),
                _num(r.net, null),
            ]
        )
    return out.getvalue()


def generate(history: History, cfg: SampleCfg, *, end: date, fmt: FormatCfg | None = None) -> dict[str, str]:
    """One CSV text per year, keyed by file name (``TxnHistory_<year>.csv``)."""
    fmt = fmt or FormatCfg()
    first_year, last_year = cfg.years
    days = sorted(d for d in history.dates if first_year <= d.year <= last_year and d <= end)
    rows = _Story(history, cfg, days).run()
    files: dict[str, str] = {}
    cash = 0.0
    for year in range(first_year, min(last_year, end.year) + 1):
        in_year = [r for r in rows if r.date.year == year]
        start = cash
        cash = round(cash + sum(r.net or 0.0 for r in in_year), 2)
        files[f"TxnHistory_{year}.csv"] = _write_year(
            year, in_year, start_cash=start, end_cash=cash, last=days[-1], fmt=fmt
        )
    return files
