"""The committed sample files (generated from real market data) load cleanly (criteria I9, A1, Q8)."""

import pytest

from folioex.config import Settings
from folioex.domain.models import TxnType
from folioex.errors import FolioExError, IssueKind
from folioex.ingestion.pipeline import ingest
from folioex.sample import loader
from folioex.sample.loader import sample_uploads


def test_one_file_per_configured_year(settings: Settings) -> None:
    first, last = settings.sample.years
    assert [u.name for u in sample_uploads()] == [f"TxnHistory_{y}.csv" for y in range(first, last + 1)]


def test_sample_set_has_zero_issues_and_every_cash_check_passes(settings: Settings) -> None:
    result = ingest(sample_uploads(), settings)
    assert result.all_issues() == []
    assert all(f.status == "ok" and f.cash_check == "ok" for f in result.files)
    assert len(result.transactions) > 500


def test_cash_identity_matches_the_last_summary(settings: Settings) -> None:
    result = ingest(sample_uploads(), settings)
    final_cash = round(sum(t.net for t in result.transactions), 2)
    last = result.files[-1].summary
    assert last is not None and last.ending_cash == pytest.approx(final_cash, abs=0.01)


def test_loading_twice_changes_nothing(settings: Settings) -> None:
    once = ingest(sample_uploads(), settings)
    twice = ingest(sample_uploads() + sample_uploads(), settings)
    assert twice.transactions == once.transactions


def test_uses_only_the_configured_tickers_and_includes_a_split(settings: Settings) -> None:
    result = ingest(sample_uploads(), settings)
    assert set(result.tickers) <= set(settings.sample.tickers)
    assert any(t.type is TxnType.SPLIT for t in result.transactions)
    assert {t.type for t in result.transactions} >= {
        TxnType.DEPOSIT,
        TxnType.BUY,
        TxnType.SELL,
        TxnType.DIVIDEND,
        TxnType.INTEREST,
        TxnType.WITHDRAWAL,
    }


def test_missing_sample_files_give_a_friendly_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr(loader, "sample_dir", lambda: tmp_path)
    with pytest.raises(FolioExError) as info:
        sample_uploads()
    assert info.value.issue.kind is IssueKind.SAMPLE_DATA_MISSING
