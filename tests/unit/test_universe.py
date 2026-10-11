"""The NYSE + Nasdaq stock list behind the Compare search (criteria C6, E3)."""

from pathlib import Path

import pytest

from folioex.config import CacheCfg
from folioex.market_data.universe import Listing, Universe, parse_universe
from tests.fakes import UNIVERSE_NASDAQ as NASDAQ
from tests.fakes import UNIVERSE_OTHER as OTHER
from tests.fakes import FakeUniverseSource as Source


def test_parsing_keeps_nyse_and_nasdaq_only() -> None:
    listings = parse_universe(NASDAQ, OTHER)
    symbols = [item.symbol for item in listings]
    assert symbols == sorted(symbols)
    assert set(symbols) == {"AAAA", "BBBB", "QQET", "CCCC", "DD-B", "EEEE", "FFFF"}  # no tests, $, or Cboe


def test_parsing_flags_funds_and_exchanges() -> None:
    by = {item.symbol: item for item in parse_universe(NASDAQ, OTHER)}
    assert by["QQET"].is_fund and by["EEEE"].is_fund and not by["AAAA"].is_fund
    assert by["AAAA"].exchange == "Nasdaq" and by["CCCC"].exchange == "NYSE"
    assert by["EEEE"].exchange == "NYSE Arca" and by["FFFF"].exchange == "NYSE American"
    assert by["AAAA"].name == "Alpha Analytics Inc."  # the " - Common Stock" suffix is noise


def test_share_classes_use_the_same_rule_as_uploads() -> None:
    assert "DD-B" in {item.symbol for item in parse_universe(NASDAQ, OTHER)}


def test_garbage_text_gives_an_empty_list() -> None:
    assert parse_universe("", "") == []
    assert parse_universe("not|a|real|file\nx", "???") == []


def uni(tmp_path: Path, source: Source, **kw: float) -> Universe:
    return Universe(source, CacheCfg(dir=tmp_path / "cache", **kw))


def test_search_ranks_exact_symbol_then_prefix_then_name(tmp_path: Path) -> None:
    u = uni(tmp_path, Source())
    assert u.search("aaaa")[0].symbol == "AAAA"
    assert next(i.symbol for i in u.search("B")) == "BBBB"  # a symbol that starts with it beats a name match
    assert [i.symbol for i in u.search("bio")] == ["BBBB"]  # matches inside the company name
    assert u.search("zzzz-nothing") == []
    assert u.search("") == []


def test_search_limit(tmp_path: Path) -> None:
    assert len(uni(tmp_path, Source()).search("a", limit=2)) == 2


def test_the_list_is_cached_for_a_day(tmp_path: Path) -> None:
    source = Source()
    uni(tmp_path, source).search("a")
    uni(tmp_path, source).search("a")  # a new process: read from disk
    assert source.calls == 1


def test_an_old_saved_list_is_refreshed_but_kept_if_offline(tmp_path: Path) -> None:
    uni(tmp_path, Source()).search("a")
    old = uni(tmp_path, Source(fail=True), universe_ttl_days=1e-9)
    assert [i.symbol for i in old.search("aaaa")] == ["AAAA"]  # offline: yesterday's list is still fine
    assert old.available


def test_no_list_and_no_internet_is_calm(tmp_path: Path) -> None:
    u = uni(tmp_path, Source(fail=True))
    assert u.search("a") == []
    assert not u.available


def test_every_listing_is_plain_data() -> None:
    item = parse_universe(NASDAQ, OTHER)[0]
    assert isinstance(item, Listing) and item.label.startswith(item.symbol)


def test_works_without_a_cache_folder() -> None:
    u = Universe(Source(), None)
    assert [i.symbol for i in u.search("aaaa")] == ["AAAA"]


@pytest.mark.parametrize("query", ["  aaaa  ", "AAAA", "aAaA"])
def test_search_ignores_case_and_spaces(tmp_path: Path, query: str) -> None:
    assert uni(tmp_path, Source()).search(query)[0].symbol == "AAAA"
