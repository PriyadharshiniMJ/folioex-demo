"""The one table component used everywhere (AG Grid). Every column header is centered."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import dash_ag_grid as dag

HEADER_CLASS = "fx-center"
Kind = Literal[
    "text", "number", "money", "money2", "money_signed", "pct", "pct_signed", "date", "compact", "ratio"
]
_NUMERIC: frozenset[str] = frozenset(
    {"number", "money", "money2", "money_signed", "pct", "pct_signed", "compact", "ratio"}
)
# Numbers must never be cut off: each kind gets a minimum width; a wide table scrolls inside its card.
_MIN_WIDTH: dict[str, int] = {
    "number": 90,
    "money": 110,
    "money2": 115,
    "money_signed": 110,
    "pct": 95,
    "pct_signed": 95,
    "date": 118,
    "compact": 105,
    "ratio": 85,
    "text": 90,
}

# d3-format is available inside dash-ag-grid function strings; it prints a true minus sign (−).
_FORMATTERS: dict[str, str] = {
    "money": "params.value == null ? '—' : d3.format('$,.0f')(params.value)",
    "money2": "params.value == null ? '—' : d3.format('$,.2f')(params.value)",
    "pct": "params.value == null ? '—' : d3.format('.1%')(params.value)",
    "money_signed": "params.value == null ? '—' : d3.format('+$,.0f')(params.value)",
    "pct_signed": "params.value == null ? '—' : d3.format('+.1%')(params.value)",
    "number": "params.value == null ? '—' : d3.format(',')(params.value)",
    # company sizes read as $3.4T / $80B; ratios (P/E, Sharpe) as 21.5
    "compact": "params.value == null ? '—' : d3.format('$.3~s')(params.value).replace('G', 'B')",
    "ratio": "params.value == null ? '—' : d3.format('.2f')(params.value)",
    # ISO dates sort correctly; show them day/month/year.
    "date": "params.value ? params.value.split('-').reverse().join('/') : '—'",
}


@dataclass(frozen=True, slots=True)
class Col:
    field: str
    header: str
    kind: Kind = "text"
    tip: str | None = None
    min_width: int | None = None
    # CSS class → JS condition written with ``x`` (the cell value) and ``data`` (the row), e.g. "x > 0"
    class_rules: Mapping[str, str] | None = None
    wrap: bool = False  # long text wraps onto several lines instead of being cut off


_SHORTHAND = re.compile(r"(?<![\w.$'\"])(x|data)(?![\w$'\"])")


def js_condition(condition: str) -> str:
    """AG Grid rules are written with ``params``: ``x`` becomes params.value, ``data`` params.data."""
    return _SHORTHAND.sub(lambda m: "params.value" if m.group(1) == "x" else "params.data", condition)


def column_defs(cols: Sequence[Col]) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for col in cols:
        d: dict[str, Any] = {
            "field": col.field,
            "headerName": col.header,
            "headerClass": HEADER_CLASS,
            "cellClass": "fx-num" if col.kind in _NUMERIC else "fx-text",
        }
        if col.kind in _FORMATTERS:
            d["valueFormatter"] = {"function": _FORMATTERS[col.kind]}
        if col.tip:
            d["headerTooltip"] = col.tip
        d["minWidth"] = col.min_width or _MIN_WIDTH[col.kind]
        if col.class_rules:
            d["cellClassRules"] = {name: js_condition(rule) for name, rule in col.class_rules.items()}
        if col.wrap:
            d |= {"wrapText": True, "autoHeight": True}
        defs.append(d)
    return defs


def base_options() -> dict[str, Any]:
    return {"domLayout": "autoHeight", "tooltipShowDelay": 200, "animateRows": False}


def data_table(
    table_id: str | Mapping[str, str],
    cols: Sequence[Col],
    rows: Sequence[Mapping[str, Any]],
    *,
    page_size: int | None = None,
    class_name: str = "",
    row_id: str | None = None,
    selectable: bool = False,
) -> dag.AgGrid:
    options = base_options()
    if page_size:
        sizes = [page_size, page_size * 2, page_size * 4]
        options |= {"pagination": True, "paginationPageSize": page_size, "paginationPageSizeSelector": sizes}
    if selectable:
        options["rowSelection"] = {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True}
    extra: dict[str, Any] = {"getRowId": f"params.data.{row_id}"} if row_id else {}
    return dag.AgGrid(
        **extra,
        id=table_id,
        columnDefs=column_defs(cols),
        rowData=list(rows),
        defaultColDef={
            "headerClass": HEADER_CLASS,
            "sortable": True,
            "resizable": True,
            "suppressMovable": True,
        },
        dashGridOptions=options,
        columnSize="responsiveSizeToFit",
        className=f"fx-grid {class_name}".strip(),
    )
