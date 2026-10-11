"""Structural rules (criteria Q7, Q8): pure layers stay Dash-free; no hardcoded tickers in code."""

import ast
from pathlib import Path

import pytest

from folioex.config import load_settings

SRC = Path(__file__).resolve().parents[2] / "src" / "folioex"
UI_FREE = [p for p in SRC.rglob("*.py") if "ui" not in p.relative_to(SRC).parts]
FORBIDDEN_IMPORTS = ("dash", "dash_bootstrap_components", "dash_ag_grid", "plotly", "folioex.ui")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", UI_FREE, ids=lambda p: str(p.relative_to(SRC)))
def test_non_ui_modules_do_not_import_ui_libraries(path: Path) -> None:
    bad = {
        m for m in imported_modules(path) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_IMPORTS)
    }
    assert not bad, f"{path.name} imports {bad}; keep finance logic Dash-free"


def test_pure_layers_exist() -> None:
    for pkg in ("analytics", "ingestion", "market_data", "jobs", "domain"):
        assert (SRC / pkg / "__init__.py").exists()


def string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_no_hardcoded_tickers_outside_config() -> None:
    s = load_settings()
    tickers = set(s.sample.tickers) | set(s.benchmark.choices)
    offenders: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if path.name == "config.py":
            continue
        hits = tickers & string_constants(path)
        if hits:
            offenders[str(path.relative_to(SRC))] = hits
    assert not offenders, f"tickers belong in config.toml, not code: {offenders}"
