"""Test helpers shared across suites."""

import re
from collections.abc import Iterator
from typing import Any

# Text that must never reach the investor's screen (criterion E2). Matched as whole words,
# so "financial" doesn't trip "nan" but a stray "NaN" or "nan" does.
FORBIDDEN_TOKENS = (
    "Traceback",
    "Exception",
    "Error:",
    "KeyError",
    "ValueError",
    "TypeError",
    "NoneType",
    "NaN",
    "nan",
    "undefined",
    "HTTP",
    '\n  File "',
)
_PATTERNS = [re.compile(rf"(?<![A-Za-z]){re.escape(tok)}(?![A-Za-z])") for tok in FORBIDDEN_TOKENS]


def assert_no_technical_text(text: str) -> None:
    found = [tok for tok, pat in zip(FORBIDDEN_TOKENS, _PATTERNS, strict=True) if pat.search(text)]
    assert not found, f"technical text leaked to the user: {found!r} in {text!r}"


def walk(component: Any) -> Iterator[Any]:
    """Yield a Dash component and every descendant."""
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "to_plotly_json"):
            yield from walk(child)


def text_of(component: Any) -> str:
    """All literal text inside a component tree, space-joined."""
    parts: list[str] = []
    for node in walk(component):
        children = getattr(node, "children", None)
        items = children if isinstance(children, (list, tuple)) else [children]
        parts.extend(str(c) for c in items if isinstance(c, (str, int, float)))
    return " ".join(parts)
