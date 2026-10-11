"""Compare maths: rebased lines, risk figures, stars, colours and limits (criteria C1–C5, C7)."""

import numpy as np
import pandas as pd
import pytest

from folioex.analytics import compare as cmp


def days(n: int, start: str = "2025-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def test_rebase_starts_at_100() -> None:
    idx = days(4)
    out = cmp.rebase(pd.Series([50.0, 55.0, 49.5, 60.0], index=idx), idx)
    assert out is not None
    assert list(out.round(6)) == [100.0, 110.0, 99.0, 120.0]


def test_rebase_starts_when_a_stock_starts_trading() -> None:
    idx = days(5)
    prices = pd.Series([np.nan, np.nan, 20.0, 22.0, 21.0], index=idx)
    out = cmp.rebase(prices, idx)
    assert out is not None and out.index[0] == idx[2] and out.iloc[0] == 100.0


def test_rebase_fills_gaps_but_not_the_start() -> None:
    idx = days(4)
    prices = pd.Series([10.0, np.nan, 12.0, np.nan], index=idx)
    out = cmp.rebase(prices, idx)
    assert out is not None
    assert list(out.round(6)) == [100.0, 100.0, 120.0, 120.0]


def test_not_enough_points_means_no_line() -> None:
    idx = days(3)
    assert cmp.rebase(pd.Series([np.nan, np.nan, 5.0], index=idx), idx) is None
    assert cmp.rebase(pd.Series(dtype=float), idx) is None


def test_period_return_and_biggest_drop_by_hand() -> None:
    idx = days(3)
    stats = cmp.stats_of(pd.Series([100.0, 110.0, 99.0], index=idx), rf=0.04)
    assert stats.ret == pytest.approx(-0.01)
    assert stats.drawdown == pytest.approx(99 / 110 - 1)  # −10%
    assert stats.annual is None  # under a year: no yearly figure
    assert stats.volatility is None and stats.sharpe is None  # too few days to say


def test_yearly_return_only_after_a_year() -> None:
    idx = pd.date_range("2024-01-01", "2025-01-01", freq="D")  # 366 days
    stats = cmp.stats_of(pd.Series(np.linspace(100, 121, len(idx)), index=idx), rf=0.0)
    assert stats.ret == pytest.approx(0.21)
    assert stats.annual == pytest.approx(1.21 ** (365 / 366) - 1)


def test_volatility_and_sharpe_match_the_definition() -> None:
    idx = days(60)
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0005, 0.01, len(idx) - 1)
    series = pd.Series(100 * np.cumprod(np.r_[1.0, 1 + returns]), index=idx)
    stats = cmp.stats_of(series, rf=0.04)
    r = series.pct_change().dropna()
    vol = float(r.std(ddof=1) * np.sqrt(252))
    assert stats.volatility == pytest.approx(vol)
    assert stats.sharpe == pytest.approx((float(r.mean()) * 252 - 0.04) / vol)


def test_a_flat_line_has_no_sharpe() -> None:
    stats = cmp.stats_of(pd.Series(100.0, index=days(40)), rf=0.04)
    assert stats.volatility == 0.0 and stats.sharpe is None and stats.drawdown == 0.0


def test_portfolio_line_ignores_deposits() -> None:
    idx = days(3)
    value = pd.Series([1000.0, 1100.0, 1650.0], index=idx)
    flows = pd.Series([1000.0, 0.0, 500.0], index=idx)  # T-A4: the 500 deposit is not growth
    line = cmp.portfolio_index(value, flows, 0, 2)
    assert list(line.round(6)) == [100.0, 110.0, 115.0]


def line(key: str, kind: str, **values: float | None) -> cmp.Line:
    base: dict[str, float | None] = {
        "ret": 0.1,
        "annual": None,
        "volatility": 0.2,
        "drawdown": -0.1,
        "sharpe": 1.0,
    }
    merged = base | values
    stats = cmp.Stats(
        ret=merged["ret"] or 0.0,
        annual=merged["annual"],
        volatility=merged["volatility"],
        drawdown=merged["drawdown"] or 0.0,
        sharpe=merged["sharpe"],
    )
    return cmp.Line(key, key, kind, pd.Series([100.0, 110.0]), stats, None, dividend_yield=values.get("dy"))


def test_stars_mark_the_best_in_each_column() -> None:
    lines = [
        line("AAA", "holding", ret=0.30, volatility=0.30, drawdown=-0.20, sharpe=0.8),
        line("BBB", "wishlist", ret=0.10, volatility=0.15, drawdown=-0.05, sharpe=1.4),
        line("PORT", "portfolio", ret=0.90, volatility=0.01, drawdown=0.0, sharpe=9.0),
        line("SPY", "benchmark", ret=0.95, volatility=0.01, drawdown=0.0, sharpe=9.0),
    ]
    stars = cmp.best_keys(lines)
    assert stars["ret"] == {"AAA"}
    assert stars["volatility"] == {"BBB"}  # calmest wins
    assert stars["drawdown"] == {"BBB"}  # smallest drop wins
    assert stars["sharpe"] == {"BBB"}


def test_ties_share_the_star_and_missing_values_never_win() -> None:
    lines = [
        line("AAA", "holding", ret=0.2, sharpe=None),
        line("BBB", "wishlist", ret=0.2, sharpe=0.5),
        line("CCC", "wishlist", ret=0.1, sharpe=None),
    ]
    stars = cmp.best_keys(lines)
    assert stars["ret"] == {"AAA", "BBB"}
    assert stars["sharpe"] == set()  # only one value: nothing to compare it with


def test_dividend_yield_star_goes_to_the_highest() -> None:
    lines = [line("AAA", "holding", dy=0.01), line("BBB", "wishlist", dy=0.03), line("CCC", "wishlist")]
    assert cmp.best_keys(lines)["dividend_yield"] == {"BBB"}


def test_a_single_pick_gets_no_stars() -> None:
    assert all(not keys for keys in cmp.best_keys([line("AAA", "holding")]).values())


def test_how_many_picks_beat_the_portfolio() -> None:
    lines = [
        line("AAA", "holding", ret=0.30),
        line("BBB", "wishlist", ret=0.05),
        line("CCC", "wishlist", ret=0.12),
        line("PORT", "portfolio", ret=0.10),
        line("SPY", "benchmark", ret=0.50),
    ]
    assert cmp.beat_count(lines) == (2, 3)
    assert cmp.beat_count([line("AAA", "holding")]) is None  # no portfolio line to beat


def test_colours_stay_with_a_ticker() -> None:
    first = cmp.assign_slots({}, ["AAA", "BBB", "CCC"], 8)
    assert sorted(first.values()) == [0, 1, 2]
    after_removal = cmp.assign_slots(first, ["AAA", "CCC"], 8)
    assert after_removal["AAA"] == first["AAA"] and after_removal["CCC"] == first["CCC"]
    assert "BBB" not in after_removal
    added = cmp.assign_slots(after_removal, ["AAA", "CCC", "DDD"], 8)
    assert added["DDD"] == first["BBB"]  # the freed colour is reused
    assert added["AAA"] == first["AAA"]


def test_never_more_colours_than_exist() -> None:
    picks = [f"S{i}" for i in range(10)]
    slots = cmp.assign_slots({}, picks, 8)
    assert len(slots) == 8 and sorted(slots.values()) == list(range(8))


def test_the_limit_drops_the_newest_pick() -> None:
    held, wish = ["A", "B", "C"], ["D", "E", "F", "G"]
    assert cmp.clamp_picks(held, [*wish, "H", "I"], 8, newest="wishlist") == (held, [*wish, "H"], True)
    assert cmp.clamp_picks([*held, "X", "Y", "Z"], wish, 8, newest="holdings") == (
        ["A", "B", "C", "X"],
        wish,
        True,
    )
    assert cmp.clamp_picks(held, wish[:2], 8, newest="wishlist") == (held, wish[:2], False)


def test_a_ticker_in_both_lists_counts_once() -> None:
    held, wish, trimmed = cmp.clamp_picks(["AAA"], ["AAA", "BBB"], 8, newest="wishlist")
    assert held == ["AAA"] and wish == ["BBB"] and not trimmed
