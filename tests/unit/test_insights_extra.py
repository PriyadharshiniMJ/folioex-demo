"""The last insight rules: tax-and-fee drag and data-quality notes (criteria A13, E2)."""

from dataclasses import replace

import pytest

from folioex.analytics.insights import PERFORMANCE_RULES, data_quality_insight, performance_insights
from folioex.analytics.performance import PerformanceView, performance_view
from folioex.config import InsightsCfg, Settings, load_settings
from folioex.errors import Issue, IssueKind, MessageCatalog, Severity
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text


@pytest.fixture(scope="module")
def view() -> PerformanceView:
    settings = load_settings()
    data = process(sample_uploads(), settings, NullProgress(), market=CachedMarketData(FakeProvider(), None))
    return performance_view(data, settings, period="1Y", benchmark="SPY")


def with_costs(
    view: PerformanceView, *, price: float, dividends: float, tax: float, fees: float
) -> PerformanceView:
    b = replace(view.breakdown, price_gains=price, dividends=dividends, withholding=-tax, fees=-fees)
    return replace(view, breakdown=b)


def keys(view: PerformanceView, cfg: InsightsCfg | None = None) -> list[str]:
    return [i.key for i in performance_insights(view, "S&P 500", "the last year", cfg or InsightsCfg())]


def test_costs_that_take_a_big_bite_are_called_out(view: PerformanceView) -> None:
    heavy = with_costs(view, price=700, dividends=300, tax=60, fees=40)  # $100 of $1,000
    insight = next(
        i
        for i in performance_insights(heavy, "S&P 500", "the last year", InsightsCfg())
        if i.key == "fee_drag"
    )
    assert insight.severity == "info"
    assert insight.context["share"] == "10%" and insight.context["amount"] == "$100"


def test_small_costs_are_left_alone(view: PerformanceView) -> None:
    light = with_costs(view, price=700, dividends=300, tax=5, fees=5)
    assert "fee_drag" not in keys(light)


def test_no_costs_no_note_and_no_division_by_zero(view: PerformanceView) -> None:
    assert "fee_drag" not in keys(with_costs(view, price=500, dividends=0, tax=0, fees=0))
    assert "fee_drag" not in keys(
        with_costs(view, price=-500, dividends=0, tax=50, fees=0)
    )  # no gains to bite


def test_the_threshold_is_a_setting(view: PerformanceView) -> None:
    mild = with_costs(view, price=700, dividends=300, tax=30, fees=20)  # 5%
    assert "fee_drag" not in keys(mild)
    assert "fee_drag" in keys(mild, InsightsCfg(fee_drag_pct=0.04))


def issue(severity: Severity, kind: IssueKind = IssueKind.HISTORY_GAP) -> Issue:
    return Issue(kind, severity)


def test_data_quality_counts_only_things_worth_a_look() -> None:
    assert data_quality_insight([]) is None
    assert data_quality_insight([issue(Severity.INFO), issue(Severity.INFO)]) is None
    one = data_quality_insight([issue(Severity.WARNING), issue(Severity.INFO)])
    many = data_quality_insight([issue(Severity.WARNING), issue(Severity.BLOCKING), issue(Severity.WARNING)])
    assert one is not None and many is not None
    assert one.severity == "warning" and one.key == "data_quality"
    assert one.context["things"] == "1 thing" and many.context["things"] == "3 things"


def test_every_new_rule_has_plain_wording(catalog: MessageCatalog, view: PerformanceView) -> None:
    assert "fee_drag" in PERFORMANCE_RULES
    heavy = with_costs(view, price=700, dividends=300, tax=60, fees=40)
    found = performance_insights(heavy, "S&P 500", "the last year", InsightsCfg())
    found.append(data_quality_insight([issue(Severity.WARNING)]))  # type: ignore[arg-type]
    for item in found:
        text = catalog.insight(item)
        assert text and "{" not in text, item.key
        assert_no_technical_text(text)


def test_the_setting_is_documented_in_config(settings: Settings) -> None:
    assert settings.insights.fee_drag_pct == 0.10
    with pytest.raises(ValueError, match="greater than 0"):
        InsightsCfg(fee_drag_pct=0)
