"""Market data: the cache, offline behaviour and the yfinance adapter (criterion E3). No network."""

import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from folioex.config import CacheCfg
from folioex.errors import IssueKind
from folioex.market_data.base import MarketDataError, PriceData
from folioex.market_data.cache import CachedMarketData
from folioex.market_data.yfinance_provider import YFinanceProvider
from tests.fakes import FakeProvider

START, END = date(2025, 1, 1), date(2025, 3, 31)


def source(tmp_path: Path, provider: FakeProvider, **cfg: object) -> CachedMarketData:
    return CachedMarketData(provider, CacheCfg(dir=tmp_path / "cache", **cfg))  # type: ignore[arg-type]


def test_price_data_helpers() -> None:
    data = PriceData.from_closes({"AAA": {date(2025, 1, 2): 10, date(2025, 1, 3): 11}})
    assert data.last("AAA") == (date(2025, 1, 3), 11)
    assert data.previous_close("AAA") == 10
    assert data.last("ZZZ") is None
    assert data.last_date() == date(2025, 1, 3)


def test_fresh_cache_avoids_the_network(tmp_path: Path) -> None:
    provider = FakeProvider()
    first = source(tmp_path, provider).load(["AAA", "BBB"], START, END)
    assert first.status == "live"
    again = source(tmp_path, provider).load(["AAA", "BBB"], START, END)
    assert len(provider.price_calls) == 1
    assert again.status == "live"
    pd.testing.assert_frame_equal(first.prices.close, again.prices.close, check_freq=False)


def test_stale_cache_is_refreshed(tmp_path: Path) -> None:
    provider = FakeProvider()
    source(tmp_path, provider).load(["AAA"], START, END)
    old = time.time() - 48 * 3600
    for f in (tmp_path / "cache" / "prices").glob("*.csv"):
        os.utime(f, (old, old))
    source(tmp_path, provider).load(["AAA"], START, END)
    assert len(provider.price_calls) == 2


def test_offline_uses_saved_prices_and_says_as_of(tmp_path: Path) -> None:
    source(tmp_path, FakeProvider()).load(["AAA"], START, END)
    old = time.time() - 48 * 3600
    for f in (tmp_path / "cache" / "prices").glob("*.csv"):
        os.utime(f, (old, old))
    snap = source(tmp_path, FakeProvider(offline=True)).load(["AAA"], START, END)
    assert snap.status == "offline"
    assert snap.prices.last("AAA") is not None
    issue = next(i for i in snap.issues if i.kind is IssueKind.MARKET_DATA_OFFLINE)
    assert issue.context == {"date": "31/03/2025"}


def test_offline_with_nothing_saved(tmp_path: Path) -> None:
    snap = source(tmp_path, FakeProvider(offline=True)).load(["AAA"], START, END)
    assert snap.status == "unavailable"
    assert [i.kind for i in snap.issues] == [IssueKind.MARKET_DATA_UNAVAILABLE]
    assert snap.prices.symbols() == []


def test_unknown_tickers_are_listed_not_fatal(tmp_path: Path) -> None:
    snap = source(tmp_path, FakeProvider(missing=["ZZZ"])).load(["AAA", "ZZZ"], START, END)
    assert snap.missing == ["ZZZ"]
    assert snap.prices.symbols() == ["AAA"]


def test_profiles_are_cached_too(tmp_path: Path) -> None:
    provider = FakeProvider()
    source(tmp_path, provider).load(["AAA"], START, END)
    snap = source(tmp_path, provider).load(["AAA"], START, END)
    assert len(provider.profile_calls) == 1
    assert snap.profiles["AAA"].sector


def test_no_cache_dir_means_nothing_is_written(tmp_path: Path) -> None:
    snap = CachedMarketData(FakeProvider(), None).load(["AAA"], START, END)
    assert snap.status == "live"
    assert not any(tmp_path.iterdir())


def test_cache_respects_its_size_cap(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "prices"
    cache.mkdir(parents=True)
    for i in range(5):
        (cache / f"OLD{i}.csv").write_text("x" * 300_000)
        os.utime(cache / f"OLD{i}.csv", (time.time() - 1000 + i, time.time() - 1000 + i))
    source(tmp_path, FakeProvider(), max_mb=1).load(["AAA"], START, END)
    total = sum(f.stat().st_size for f in (tmp_path / "cache").rglob("*") if f.is_file())
    assert total <= 1024 * 1024
    assert (cache / "AAA.csv").exists()


def test_progress_is_reported(tmp_path: Path) -> None:
    seen: list[tuple[str, int, int]] = []

    class Rec:
        def stage(self, key: str) -> None:
            seen.append((key, 0, 0))

        def sub(self, done: int, total: int, unit: str = "") -> None:
            seen.append((unit, done, total))

    source(tmp_path, FakeProvider()).load(["AAA", "BBB"], START, END, progress=Rec(), benchmarks=["SPY"])
    stages = [
        k
        for k, _, _ in seen
        if k in ("fetch_prices", "fetch_benchmark", "fetch_dividends_splits", "fetch_profiles")
    ]
    assert stages == ["fetch_prices", "fetch_benchmark", "fetch_dividends_splits", "fetch_profiles"]
    assert ("stocks", 2, 2) in seen


# ---- the yfinance adapter, with yfinance itself replaced ----


def fake_frame(symbols: list[str]) -> pd.DataFrame:
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    cols = pd.MultiIndex.from_product([symbols, ["Close", "Adj Close", "Dividends", "Stock Splits"]])
    data = []
    for _ in idx:
        row = []
        for _s in symbols:
            row += [10.0, 9.5, 0.0, 0.0]
        data.append(row)
    frame = pd.DataFrame(data, index=idx, columns=cols)
    frame.loc[idx[1], (symbols[0], "Dividends")] = 0.25
    frame.loc[idx[1], (symbols[0], "Stock Splits")] = 2.0
    return frame


def test_yfinance_adapter_parses_closes_dividends_and_splits(monkeypatch: pytest.MonkeyPatch) -> None:
    import folioex.market_data.yfinance_provider as mod

    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: fake_frame(["AAA", "BBB"]))
    data = YFinanceProvider().prices(["AAA", "BBB", "ZZZ"], START, END)
    assert data.symbols() == ["AAA", "BBB"]
    assert data.last("AAA") == (date(2025, 1, 3), 10.0)
    assert data.dividends["AAA"] == [(date(2025, 1, 3), 0.25)]
    assert data.splits["AAA"] == [(date(2025, 1, 3), 2.0)]
    assert float(data.adj_close["AAA"].iloc[-1]) == 9.5


def test_yfinance_adapter_turns_empty_results_into_a_market_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import folioex.market_data.yfinance_provider as mod

    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: pd.DataFrame())
    with pytest.raises(MarketDataError):
        YFinanceProvider().prices(["AAA"], START, END)


def test_yfinance_profiles_map_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import folioex.market_data.yfinance_provider as mod

    infos = {
        "AAA": {
            "longName": "Alpha Corp",
            "sector": "Technology",
            "industry": "Chips",
            "marketCap": 3e11,
            "quoteType": "EQUITY",
            "trailingPE": 31.5,
            "dividendYield": 0.52,
        },
        "FFF": {"shortName": "Fund", "quoteType": "ETF", "dividendYield": 1.4},
    }

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.info = infos[symbol]

    monkeypatch.setattr(mod.yf, "Ticker", FakeTicker)
    out = YFinanceProvider().profiles(["AAA", "FFF"])
    assert (out["AAA"].name, out["AAA"].sector, out["AAA"].market_cap) == ("Alpha Corp", "Technology", 3e11)
    assert out["AAA"].dividend_yield == pytest.approx(0.0052)  # yfinance gives percent
    assert out["FFF"].is_fund


@pytest.mark.live
@pytest.mark.enable_socket
def test_live_yfinance_smoke() -> None:  # pragma: no cover - needs the internet
    data = YFinanceProvider().prices(["SPY"], date(2025, 1, 2), date(2025, 1, 31))
    assert data.last("SPY") is not None
