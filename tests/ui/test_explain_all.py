"""The 💡 Explain panel on every tab: plain, personal and in step with the screen (criteria A13, U1, U10)."""

import pytest
from dash import html

from folioex.config import Settings, load_settings
from folioex.dataset import Dataset
from folioex.errors import MessageCatalog
from folioex.formatting import percent, plural
from folioex.jobs.processing import process
from folioex.market_data.cache import CachedMarketData
from folioex.progress import NullProgress
from folioex.sample.loader import sample_uploads
from folioex.ui.layout import TABS, explain_content
from folioex.ui.tabs import compare, data_hub, holdings, performance
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text, text_of

MARKET = CachedMarketData(FakeProvider(), None)


@pytest.fixture(scope="module")
def data() -> Dataset:
    return process(sample_uploads(), load_settings(), NullProgress(), market=MARKET)


def build(tab: str, data: Dataset, settings: Settings, catalog: MessageCatalog, **kw: object) -> html.Div:
    if tab == "datahub":
        return data_hub.explain(data.ingest, catalog, settings.format.date_format)
    if tab == "holdings":
        return holdings.explain(data, catalog, settings)
    if tab == "performance":
        args = {"period": "1Y", "benchmark": "SPY", "custom": None} | kw
        return performance.explain(data, catalog, settings, **args)  # type: ignore[arg-type]
    args = {
        "holdings": [r.symbol for r in data.holdings.rows[:2]],
        "wishlist": ["AAA"],
        "period": "1Y",
        "benchmark": "SPY",
        "show_portfolio": True,
        "show_benchmark": False,
    } | kw
    return compare.explain(data, MARKET, catalog, settings, **args)  # type: ignore[arg-type]


@pytest.mark.parametrize("tab", [t.key for t in TABS])
def test_every_tab_explains_itself_with_the_same_shape(
    tab: str, data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    text = text_of(build(tab, data, settings, catalog))
    for heading in ("In a nutshell", "What each part shows", "Words to know"):
        assert heading in text, (tab, heading)
    assert catalog.text("explain_footer") in text
    assert_no_technical_text(text)
    assert "nan" not in text.lower().split() and "None" not in text


@pytest.mark.parametrize("tab", [t.key for t in TABS])
def test_before_any_data_it_explains_how_to_start(tab: str, catalog: MessageCatalog) -> None:
    text = text_of(explain_content(tab, loaded=False, catalog=catalog))
    assert "In a nutshell" in text and "How to start" in text and "Data Hub" in text
    assert catalog.text("explain_footer") in text
    assert_no_technical_text(text)


def test_the_numbers_quoted_match_the_screen(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    view = data.holdings
    text = text_of(build("holdings", data, settings, catalog))
    assert f"${view.account_value:,.0f}" in text
    assert percent(view.total_gain_pct, decimals=1, sign=True) in text

    result = data.ingest
    hub = text_of(build("datahub", data, settings, catalog))
    assert plural(len(result.loaded_files), "file") in hub
    assert f"{len(result.transactions):,}" in hub

    kpis = {
        k.label: k.value
        for k in performance.kpis(
            performance.performance_view(data, settings, period="1Y", benchmark="SPY"), "SPY"
        )
    }
    shown = kpis["Return"].replace("▲ ", "").replace("▼ ", "")
    assert shown in text_of(build("performance", data, settings, catalog))


def test_a_different_period_gives_a_different_summary(
    data: Dataset, settings: Settings, catalog: MessageCatalog
) -> None:
    year = text_of(build("performance", data, settings, catalog, period="1Y"))
    quarter = text_of(build("performance", data, settings, catalog, period="3M"))
    assert year != quarter
    assert "the last year" in year and "the last 3 months" in quarter
    cmp_year = text_of(build("compare", data, settings, catalog, period="1Y"))
    cmp_quarter = text_of(build("compare", data, settings, catalog, period="3M"))
    assert cmp_year != cmp_quarter


def test_every_tab_has_wording_for_the_empty_state() -> None:
    import tomllib
    from importlib import resources

    words = tomllib.loads(resources.files("folioex").joinpath("explain.toml").read_text(encoding="utf-8"))
    for tab in TABS:
        assert words["empty"][tab.key]["nutshell"].strip(), tab.key
