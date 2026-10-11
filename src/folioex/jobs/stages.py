"""The 25 named processing stages shown as candles in the progress bar."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stage:
    key: str
    label: str


STAGES: tuple[Stage, ...] = (
    Stage("read_files", "Read files"),
    Stage("detect_sections", "Detect sections"),
    Stage("validate_headers", "Validate headers"),
    Stage("parse_dates", "Parse dates"),
    Stage("map_types", "Map transaction types"),
    Stage("normalize_symbols", "Normalize symbols"),
    Stage("merge_files", "Merge files"),
    Stage("remove_overlaps", "Remove overlaps"),
    Stage("reconcile_cash", "Reconcile cash"),
    Stage("detect_gaps", "Detect history gaps"),
    Stage("fetch_prices", "Fetch prices"),
    Stage("fetch_benchmark", "Fetch benchmark"),
    Stage("fetch_dividends_splits", "Fetch dividends & splits"),
    Stage("fetch_profiles", "Fetch company profiles"),
    Stage("apply_splits", "Apply splits"),
    Stage("build_lots", "Build FIFO lots"),
    Stage("replay_ledger", "Replay cash ledger"),
    Stage("daily_valuation", "Daily valuation"),
    Stage("realized_gains", "Realized gains"),
    Stage("dividend_income", "Dividend income"),
    Stage("twr", "Time-weighted return"),
    Stage("xirr", "Money-weighted return (XIRR)"),
    Stage("benchmark", "Benchmark comparison"),
    Stage("allocation", "Allocation & concentration"),
    Stage("insights_quiz", "Insights & quiz"),
)

_INDEX = {stage.key: i for i, stage in enumerate(STAGES)}


def stage_index(key: str) -> int:
    return _INDEX[key]
