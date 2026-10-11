"""Shared table wrapper: every column header is centered (criterion U2)."""

from pathlib import Path

from folioex.ui.components.data_table import Col, column_defs, data_table, js_condition

ASSETS = Path(__file__).resolve().parents[2] / "src" / "folioex" / "ui" / "assets"


def test_all_headers_centered_and_numbers_right_aligned() -> None:
    grid = data_table(
        "t",
        [
            Col("ticker", "Ticker"),
            Col("value", "Value", kind="money"),
            Col("w", "Weight", kind="pct", tip="Share"),
        ],
        [{"ticker": "AAA", "value": 1.0, "w": 0.5}],
    )
    assert grid.defaultColDef["headerClass"] == "fx-center"
    for col in grid.columnDefs:
        assert col.get("headerClass", grid.defaultColDef["headerClass"]) == "fx-center"
    by_field = {c["field"]: c for c in grid.columnDefs}
    assert by_field["value"]["cellClass"] == "fx-num"
    assert by_field["ticker"]["cellClass"] == "fx-text"
    assert by_field["w"]["headerTooltip"] == "Share"


def test_grid_is_sortable_and_themed() -> None:
    grid = data_table("t", [Col("a", "A")], [])
    assert grid.defaultColDef["sortable"] is True
    assert "fx-grid" in grid.className


def test_css_fallback_centers_grid_headers() -> None:
    css = (ASSETS / "folioex.css").read_text(encoding="utf-8")
    assert ".ag-header-cell-label" in css
    assert "justify-content: center" in css


def test_number_columns_have_a_minimum_width_so_digits_are_never_cut() -> None:
    grid = data_table(
        "t", [Col("v", "Value", kind="money2"), Col("p", "Weight", kind="pct"), Col("n", "Name")], []
    )
    widths = {c["field"]: c["minWidth"] for c in grid.columnDefs}
    assert widths["v"] >= 110 and widths["p"] >= 90 and widths["n"] >= 80


def test_class_rules_are_written_for_ag_grid() -> None:
    assert js_condition("x > 0") == "params.value > 0"
    assert js_condition("x && x.startsWith('x')") == "params.value && params.value.startsWith('x')"
    assert js_condition("data.stars && data.stars.includes('data')") == (
        "params.data.stars && params.data.stars.includes('data')"
    )
    assert js_condition("max > 1") == "max > 1"  # other identifiers are left alone
    col = Col("g", "Gain", kind="pct_signed", class_rules={"fx-up": "x > 0"})
    assert column_defs([col])[0]["cellClassRules"] == {"fx-up": "params.value > 0"}
