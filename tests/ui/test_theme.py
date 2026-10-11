"""Chart colours in Python must match the CSS tokens, in both themes."""

import re
from pathlib import Path

import pytest

from folioex.ui.theme import CHART_TOKENS, chart_colors

CSS = (Path(__file__).resolve().parents[2] / "src" / "folioex" / "ui" / "assets" / "folioex.css").read_text(
    encoding="utf-8"
)


def css_tokens(selector: str) -> dict[str, str]:
    block = CSS.split(selector, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block))


@pytest.mark.parametrize(
    ("theme", "selector"), [("light", ":root {"), ("dark", ':root[data-theme="dark"] {')]
)
def test_python_chart_colours_match_css(theme: str, selector: str) -> None:
    tokens = css_tokens(selector)
    colors = chart_colors(theme)
    for name in CHART_TOKENS:
        assert colors[name] == tokens[name].strip(), name


def test_unknown_theme_falls_back_to_light() -> None:
    assert chart_colors("purple") == chart_colors("light")
