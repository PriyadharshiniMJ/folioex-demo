"""Processing stages behind the candlestick progress bar (criterion P1)."""

from folioex.jobs.stages import STAGES, stage_index

SPEC_ORDER = [
    "Read files",
    "Detect sections",
    "Validate headers",
    "Parse dates",
    "Map transaction types",
    "Normalize symbols",
    "Merge files",
    "Remove overlaps",
    "Reconcile cash",
    "Detect history gaps",
    "Fetch prices",
    "Fetch benchmark",
    "Fetch dividends & splits",
    "Fetch company profiles",
    "Apply splits",
    "Build FIFO lots",
    "Replay cash ledger",
    "Daily valuation",
    "Realized gains",
    "Dividend income",
    "Time-weighted return",
    "Money-weighted return (XIRR)",
    "Benchmark comparison",
    "Allocation & concentration",
    "Insights & quiz",
]


def test_exactly_25_stages_in_spec_order() -> None:
    assert [s.label for s in STAGES] == SPEC_ORDER


def test_keys_are_unique_and_indexable() -> None:
    keys = [s.key for s in STAGES]
    assert len(set(keys)) == 25
    assert all(stage_index(k) == i for i, k in enumerate(keys))
