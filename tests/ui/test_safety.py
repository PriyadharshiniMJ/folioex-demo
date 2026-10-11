"""Every callback is wrapped so failures become friendly messages (criteria E2)."""

import logging

import pytest
from dash.exceptions import PreventUpdate

from folioex.errors import FolioExError, IssueKind, Severity, UserMessage
from folioex.ui.safety import friendly_alert, safe_callback
from tests.helpers import assert_no_technical_text, text_of


def fallback(msg: UserMessage) -> tuple[str, str]:
    return ("FALLBACK", msg.text)


def test_unexpected_exception_becomes_friendly_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    @safe_callback(fallback)
    def broken() -> tuple[str, str]:
        raise KeyError("x")

    with caplog.at_level(logging.ERROR, logger="folioex"):
        out = broken()
    assert out[0] == "FALLBACK"
    assert_no_technical_text(out[1])
    assert "KeyError" in caplog.text  # details are kept for the log


def test_prevent_update_passes_through() -> None:
    @safe_callback(fallback)
    def quiet() -> tuple[str, str]:
        raise PreventUpdate

    with pytest.raises(PreventUpdate):
        quiet()


def test_folioex_error_uses_its_specific_message() -> None:
    @safe_callback(fallback)
    def bad_file() -> tuple[str, str]:
        raise FolioExError(IssueKind.FILE_NOT_CSV, file="report.pdf")

    out = bad_file()
    assert "report.pdf" in out[1]
    assert_no_technical_text(out[1])


def test_success_is_untouched() -> None:
    @safe_callback(fallback)
    def fine(x: int) -> int:
        return x + 1

    assert fine(1) == 2


@pytest.mark.parametrize(
    ("severity", "color"),
    [(Severity.INFO, "info"), (Severity.WARNING, "warning"), (Severity.BLOCKING, "danger")],
)
def test_friendly_alert_maps_severity(severity: Severity, color: str) -> None:
    alert = friendly_alert(UserMessage("Something happened.", severity))
    assert alert.color == color
    assert "Something happened." in text_of(alert)
