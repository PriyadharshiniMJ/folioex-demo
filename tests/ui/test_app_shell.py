"""App shell: tabs, top bar, explain panel, disclaimer, run options (criteria E5, U1, U9, U10)."""

import dash_bootstrap_components as dbc
from dash import dcc

from folioex.config import Settings
from folioex.errors import MessageCatalog
from folioex.ui.app import create_app, run_options
from folioex.ui.layout import TABS, explain_content, page_titles
from tests.helpers import assert_no_technical_text, text_of, walk


def find_by_id(root: object, wanted: object) -> object:
    for node in walk(root):
        if getattr(node, "id", None) == wanted:
            return node
    raise AssertionError(f"no component with id {wanted!r}")


def test_run_options_never_show_dash_dev_tools(settings: Settings) -> None:
    opts = run_options(settings)
    assert opts["debug"] is False
    assert opts["dev_tools_ui"] is False
    assert opts["dev_tools_props_check"] is False
    assert (opts["host"], opts["port"]) == ("127.0.0.1", 8050)


def test_four_tabs_in_order(settings: Settings) -> None:
    layout = create_app(settings).layout
    tabs = find_by_id(layout, "tabs")
    assert isinstance(tabs, dbc.Tabs)
    assert [t.tab_id for t in tabs.children] == ["datahub", "holdings", "performance", "compare"]
    assert [t.label for t in tabs.children] == ["Data Hub", "Holdings", "Performance", "Compare"]
    assert [t.key for t in TABS] == ["datahub", "holdings", "performance", "compare"]


def test_every_tab_has_an_explain_button(settings: Settings) -> None:
    layout = create_app(settings).layout
    for tab in ("datahub", "holdings", "performance", "compare"):
        button = find_by_id(layout, {"type": "explain-open", "tab": tab})
        assert "Explain" in text_of(button)


def test_top_bar_has_name_tagline_theme_and_no_benchmark_picker(settings: Settings) -> None:
    topbar = find_by_id(create_app(settings).layout, "topbar")
    text = text_of(topbar)
    assert "FolioEx" in text
    assert "Your portfolio, explained." in text
    find_by_id(topbar, "theme-toggle")
    assert not [n for n in walk(topbar) if isinstance(n, (dcc.Dropdown, dbc.Select))]


def test_footer_disclaimer_and_full_disclaimer_modal(settings: Settings, catalog: MessageCatalog) -> None:
    layout = create_app(settings).layout
    footer = find_by_id(layout, "footer-disclaimer")
    assert catalog.text("disclaimer_footer") in text_of(footer)
    find_by_id(footer, "disclaimer-open")
    modal = find_by_id(layout, "disclaimer-modal")
    assert "Not investment advice" in text_of(modal)


def test_data_tabs_show_empty_state_until_data_is_loaded(settings: Settings) -> None:
    layout = create_app(settings).layout
    for tab in ("holdings", "performance", "compare"):
        empty = find_by_id(layout, {"type": "empty-state", "tab": tab})
        assert "Go to Data Hub" in text_of(empty)
        assert "Load sample data" in text_of(find_by_id(empty, {"type": "load-sample", "from": tab}))


def test_page_titles_include_section(settings: Settings) -> None:
    assert page_titles(settings) == {
        "datahub": "Data Hub · FolioEx",
        "holdings": "Holdings · FolioEx",
        "performance": "Performance · FolioEx",
        "compare": "Compare · FolioEx",
    }


def test_explain_panel_before_data_is_loaded(settings: Settings, catalog: MessageCatalog) -> None:
    for tab in ("datahub", "holdings", "performance", "compare"):
        content = explain_content(tab, loaded=False)
        text = text_of(content)
        assert "In a nutshell" in text
        assert "How to start" in text
        assert_no_technical_text(text)
    assert catalog.text("explain_footer")


def test_explain_unknown_tab_is_friendly() -> None:
    text = text_of(explain_content("nope", loaded=False))
    assert text.strip()
    assert_no_technical_text(text)


def test_assets_folder_is_served(settings: Settings) -> None:
    app = create_app(settings)
    assert app.config.assets_folder.endswith("assets")
    assert app.title == "FolioEx"
