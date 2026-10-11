"""Callback safety net: every Dash callback is wrapped so failures become friendly messages.

The full traceback goes to the log file; the investor sees plain English from ``messages.toml``.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import dash_bootstrap_components as dbc
from dash import html
from dash.exceptions import PreventUpdate

from folioex.errors import FolioExError, Issue, IssueKind, Severity, UserMessage, default_catalog

logger = logging.getLogger("folioex.ui")

P = ParamSpec("P")
R = TypeVar("R")

_ALERT_STYLE = {
    Severity.INFO: ("info", "ℹ"),
    Severity.WARNING: ("warning", "⚠"),
    Severity.BLOCKING: ("danger", "✗"),
}


# Classic TypeVars on purpose: P is bound per decorated callback, not by safe_callback itself.
def safe_callback(fallback: Callable[[UserMessage], R]) -> Callable[[Callable[P, R]], Callable[P, R]]:  # noqa: UP047
    """Wrap a callback; on failure log details and return ``fallback(message)`` instead of raising."""

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return fn(*args, **kwargs)
            except PreventUpdate:
                raise
            except FolioExError as exc:
                logger.warning("Callback %s reported %s", fn.__qualname__, exc.issue.kind.value)
                message = default_catalog().render(exc.issue)
            except Exception:
                logger.exception("Callback %s failed", fn.__qualname__)
                message = default_catalog().render(Issue(IssueKind.UNEXPECTED))
            return fallback(message)

        return wrapper

    return decorate


def friendly_alert(message: UserMessage) -> dbc.Alert:
    color, icon = _ALERT_STYLE[message.severity]
    hidden: dict[str, Any] = {"aria-hidden": "true"}
    return dbc.Alert(
        [html.Span(icon, className="fx-alert-icon", **hidden), html.Span(message.text)],
        color=color,
        className="fx-alert",
    )
