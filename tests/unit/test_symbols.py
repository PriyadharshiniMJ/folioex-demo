"""Rule-based symbol normalization (criterion I7)."""

import pytest

from folioex.ingestion.symbols import normalize_symbol


@pytest.mark.parametrize("raw", ["BRK B", "BRK.B", "brk.b", " BRK  B ", "BRK-B"])
def test_share_class_separators_become_hyphens(raw: str) -> None:
    assert normalize_symbol(raw, {}, "-") == "BRK-B"


@pytest.mark.parametrize("raw", ["-", "", "   ", None])
def test_empty_symbols_are_none(raw: str | None) -> None:
    assert normalize_symbol(raw, {}, "-") is None


def test_override_wins_before_and_after_normalizing() -> None:
    overrides = {"FB": "META", "ABC.W": "ABCW"}
    assert normalize_symbol("fb", overrides, "-") == "META"
    assert normalize_symbol("ABC.W", overrides, "-") == "ABCW"
    assert normalize_symbol("ABC W", overrides, "-") == "ABCW"


def test_plain_tickers_are_uppercased_only() -> None:
    assert normalize_symbol("aaa", {}, "-") == "AAA"
