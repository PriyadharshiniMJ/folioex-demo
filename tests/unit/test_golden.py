"""The sample files' headline numbers, pinned. A change here must be deliberate (``--update-golden``)."""

import json
from pathlib import Path
from typing import Any

from folioex.analytics.performance import performance_view
from folioex.config import load_settings
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from tests.fakes import FakeProvider

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "sample_kpis.json"


def snapshot() -> dict[str, Any]:
    settings = load_settings()
    data = process(sample_uploads(), settings, NullProgress(), market=CachedMarketData(FakeProvider(), None))
    h = data.holdings
    out: dict[str, Any] = {
        "files": len(data.ingest.loaded_files),
        "transactions": len(data.ingest.transactions),
        "holdings": len(h.rows),
        "account_value": round(h.account_value, 2),
        "net_deposits": round(h.net_deposits, 2),
        "total_gain": round(h.total_gain, 2),
        "total_gain_pct": round(h.total_gain_pct or 0.0, 6),
        "cash": round(h.cash, 2),
        "warnings": sum(1 for i in data.all_issues() if i.severity.value != "info"),
    }
    for period in ("1Y", "All"):
        v = performance_view(data, settings, period=period, benchmark="SPY")
        out[period] = {
            "twr": round(v.twr, 6),
            "bench_twr": round(v.bench_twr or 0.0, 6),
            "xirr": None if v.xirr is None else round(v.xirr, 6),
            "dividends_net": round(v.income.net, 2),
            "realized": round(v.realized, 2),
            "best": [p.symbol for p in v.best],
            "worst": [p.symbol for p in v.worst],
        }
    return out


def test_sample_kpis_match_the_golden_file(update_golden: bool) -> None:
    now = snapshot()
    if update_golden:
        GOLDEN.write_text(json.dumps(now, indent=2) + chr(10), encoding="utf-8")
    assert GOLDEN.exists(), "run once with --update-golden to create the golden file"
    assert now == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_snapshot_is_repeatable() -> None:
    assert snapshot() == snapshot()
