"""The sample-data generator, on synthetic prices (no network). Its output must load with zero issues."""

import csv
import io
from datetime import date, timedelta

import pytest

from folioex.config import SampleCfg, Settings
from folioex.domain.models import TxnType
from folioex.ingestion.pipeline import Upload, ingest
from folioex.sample.generator import History, generate

START, END = date(2021, 1, 1), date(2023, 12, 29)
SPLIT_DAY = date(2022, 6, 15)


def trading_days() -> list[date]:
    days, d = [], START
    while d <= END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def synthetic_history() -> History:
    days = trading_days()
    n = len(days)
    close = {
        "AAA": [50 + 30 * i / n for i in range(n)],
        "BBB": [50 + 10 * i / n for i in range(n)],  # split-adjusted, like yfinance
        "CCC": [20.0 for _ in range(n)],
    }
    quarters = [
        d for d in days if d.month in (3, 6, 9, 12) and d.day >= 10 and d.day <= 14 and d.weekday() == 1
    ]
    return History(
        dates=days,
        close=close,
        dividends={"CCC": [(d, 0.2) for d in quarters], "BBB": [(date(2021, 9, 14), 0.25)]},
        splits={"BBB": [(SPLIT_DAY, 2.0)]},
    )


def cfg(**changes: object) -> SampleCfg:
    base = {
        "years": (2021, 2023),
        "tickers": ("AAA", "BBB", "CCC"),
        "monthly_deposit": 1000.0,
        "withholding_rate": 0.0,
        "seed": 7,
    }
    return SampleCfg(**(base | changes))  # type: ignore[arg-type]


def load(files: dict[str, str], settings: Settings) -> object:
    return ingest([Upload(name, text.encode()) for name, text in files.items()], settings)


def rows(text: str) -> list[list[str]]:
    return [r[2:] for r in csv.reader(io.StringIO(text)) if r[:2] == ["Transaction History", "Data"]]


def test_one_file_per_year(settings: Settings) -> None:
    files = generate(synthetic_history(), cfg(), end=END)
    assert list(files) == ["TxnHistory_2021.csv", "TxnHistory_2022.csv", "TxnHistory_2023.csv"]


def test_generated_history_loads_with_zero_issues(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(), end=END), settings)
    assert result.all_issues() == []  # type: ignore[attr-defined]
    assert all(f.status == "ok" and f.cash_check == "ok" for f in result.files)  # type: ignore[attr-defined]


def test_every_kind_of_activity_appears(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(), end=END), settings)
    seen = {t.type for t in result.transactions}  # type: ignore[attr-defined]
    assert {
        TxnType.DEPOSIT,
        TxnType.BUY,
        TxnType.SELL,
        TxnType.DIVIDEND,
        TxnType.SPLIT,
        TxnType.INTEREST,
        TxnType.FEE,
        TxnType.WITHDRAWAL,
    } <= seen


def test_split_row_adds_shares_and_trades_use_real_prices(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(), end=END), settings)
    txns = result.transactions  # type: ignore[attr-defined]
    split = next(t for t in txns if t.type is TxnType.SPLIT)
    held_before = sum(t.quantity for t in txns if t.symbol == "BBB" and t.date < SPLIT_DAY)
    assert (split.symbol, split.date, split.quantity) == ("BBB", SPLIT_DAY, held_before)
    early_bbb_buy = next(
        t for t in txns if t.symbol == "BBB" and t.type is TxnType.BUY and t.date < SPLIT_DAY
    )
    assert early_bbb_buy.price is not None and early_bbb_buy.price > 99  # 2 × the adjusted ~50


def test_rows_are_newest_first_and_periods_cover_the_year() -> None:
    files = generate(synthetic_history(), cfg(), end=END)
    for name, text in files.items():
        dates = [tuple(reversed(r[0].split("/"))) for r in rows(text)]
        assert dates == sorted(dates, reverse=True), name
    assert "January 1, 2023 - December 29, 2023" in files["TxnHistory_2023.csv"]


def test_deterministic_for_a_seed() -> None:
    assert generate(synthetic_history(), cfg(), end=END) == generate(synthetic_history(), cfg(), end=END)
    assert generate(synthetic_history(), cfg(), end=END) != generate(
        synthetic_history(), cfg(seed=8), end=END
    )


def test_only_configured_tickers(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(), end=END), settings)
    assert set(result.tickers) <= {"AAA", "BBB", "CCC"}  # type: ignore[attr-defined]


def test_withholding_rate_adds_tax_rows(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(withholding_rate=0.15), end=END), settings)
    txns = result.transactions  # type: ignore[attr-defined]
    dividends = [t for t in txns if t.type is TxnType.DIVIDEND]
    taxes = [t for t in txns if t.type is TxnType.WITHHOLDING]
    assert len(taxes) == len(dividends)
    for div, tax in zip(dividends, taxes, strict=True):
        assert tax.net == pytest.approx(-round(div.net * 0.15, 2))
    assert result.all_issues() == []  # type: ignore[attr-defined]


def test_cash_never_goes_negative(settings: Settings) -> None:
    result = load(generate(synthetic_history(), cfg(), end=END), settings)
    cash = 0.0
    for t in result.transactions:  # type: ignore[attr-defined]
        cash = round(cash + t.net, 2)
        assert cash >= 0, t
