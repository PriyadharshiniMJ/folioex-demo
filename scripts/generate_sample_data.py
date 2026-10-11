"""Regenerate the bundled sample files from real market data (needs the internet; run rarely).

Usage:  uv run python scripts/generate_sample_data.py

Tickers, years, deposit size, withholding rate and seed come from [sample] in config.toml.
The files are written to src/folioex/sample/files/ and committed, so the app and its tests never need
the network to use them.
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from folioex.config import ConfigError, load_settings
from folioex.sample.generator import History, generate

OUT = Path(__file__).resolve().parents[1] / "src" / "folioex" / "sample" / "files"


def _clean(value: object) -> float | None:
    number = float(value) if value is not None else math.nan  # type: ignore[arg-type]
    return None if math.isnan(number) else number


def fetch(tickers: list[str], first_year: int) -> History:
    frame = yf.download(
        tickers,
        start=f"{first_year}-01-01",
        end=(date.today() + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        actions=True,
        group_by="ticker",
        progress=False,
        threads=True,
    )
    if frame is None or frame.empty:
        raise RuntimeError("no market data came back")
    dates = [pd.Timestamp(ts).date() for ts in frame.index]
    close: dict[str, list[float | None]] = {}
    dividends: dict[str, list[tuple[date, float]]] = {}
    splits: dict[str, list[tuple[date, float]]] = {}
    for t in tickers:
        if t not in frame.columns.get_level_values(0):
            print(f"  ! no data for {t}; it will be left out")
            continue
        data = frame[t]
        close[t] = [_clean(v) for v in data["Close"]]
        dividends[t] = [(d, float(v)) for d, v in zip(dates, data["Dividends"], strict=True) if v and v > 0]
        splits[t] = [(d, float(v)) for d, v in zip(dates, data["Stock Splits"], strict=True) if v and v > 0]
    # Keep only days with at least one price (drops holidays that slipped in).
    keep = [i for i in range(len(dates)) if any(close[t][i] is not None for t in close)]
    return History(
        dates=[dates[i] for i in keep],
        close={t: [values[i] for i in keep] for t, values in close.items()},
        dividends=dividends,
        splits=splits,
    )


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc.user_message, file=sys.stderr)
        return 2
    cfg = settings.sample
    print(f"Fetching {len(cfg.tickers)} tickers from {cfg.years[0]} …")
    try:
        history = fetch(list(cfg.tickers), cfg.years[0])
    except Exception as exc:  # network or service problem: say so plainly
        print(f"Couldn't download market data ({exc}). Check your internet connection and try again.")
        return 1
    end = history.dates[-1]
    files = generate(history, cfg, end=end, fmt=settings.format)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("TxnHistory_*.csv"):
        old.unlink()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
        rows = sum(1 for line in text.splitlines() if line.startswith(f"{settings.format.section},Data,"))
        print(f"  {name}: {rows} transactions")
    split_rows = sum(text.count(",Stock Split,") for text in files.values())
    print(f"Done. Data runs to {end:%d/%m/%Y}; {split_rows} split row(s). Files in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
