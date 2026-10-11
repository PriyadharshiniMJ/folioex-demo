"""Message catalog and typed issues (criteria E1, E2)."""

import logging

import pytest

from folioex.errors import FolioExError, Issue, IssueKind, MessageCatalog, Severity
from tests.helpers import assert_no_technical_text


def sample_context(catalog: MessageCatalog, kind: IssueKind) -> dict[str, object]:
    return {name: (1234.5 if spec else "sample.csv") for name, spec in catalog.placeholders(kind)}


def test_every_issue_kind_has_a_message(catalog: MessageCatalog) -> None:
    missing = [k for k in IssueKind if not catalog.has(k)]
    assert not missing


def test_catalog_has_no_orphan_entries(catalog: MessageCatalog) -> None:
    assert set(catalog.issue_keys()) == {k.value for k in IssueKind}


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_message_renders_cleanly(catalog: MessageCatalog, kind: IssueKind) -> None:
    msg = catalog.render(Issue(kind, Severity.WARNING, sample_context(catalog, kind)))
    assert msg.text.strip()
    assert "{" not in msg.text
    assert "}" not in msg.text
    assert_no_technical_text(msg.text)
    assert msg.severity is Severity.WARNING


def test_missing_context_falls_back_to_generic(
    catalog: MessageCatalog, caplog: pytest.LogCaptureFixture
) -> None:
    generic = catalog.render(Issue(IssueKind.UNEXPECTED)).text
    with caplog.at_level(logging.WARNING, logger="folioex"):
        msg = catalog.render(Issue(IssueKind.FILE_NOT_CSV))  # needs {file}
    assert msg.text == generic
    assert "file_not_csv" in caplog.text


def test_kind_missing_from_catalog_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    tiny = MessageCatalog({"issues": {"unexpected": "Something went wrong."}, "text": {}, "lines": {}})
    with caplog.at_level(logging.WARNING, logger="folioex"):
        msg = tiny.render(Issue(IssueKind.FILE_NOT_CSV, context={"file": "a.pdf"}))
    assert msg.text == "Something went wrong."
    assert "file_not_csv" in caplog.text


def test_specific_message_includes_context(catalog: MessageCatalog) -> None:
    msg = catalog.render(Issue(IssueKind.FILE_NOT_CSV, Severity.BLOCKING, {"file": "report.pdf"}))
    assert "report.pdf" in msg.text
    assert "CSV" in msg.text


def test_folioex_error_carries_issue() -> None:
    err = FolioExError(IssueKind.MISSING_COLUMN, file="2024.csv", column="Net Amount")
    assert err.issue.kind is IssueKind.MISSING_COLUMN
    assert err.issue.severity is Severity.BLOCKING
    assert err.issue.context["column"] == "Net Amount"


def test_text_entries_and_rotating_lines(catalog: MessageCatalog) -> None:
    assert "not investment" in catalog.text("disclaimer_footer")
    lines = catalog.lines("quiz_wrong")
    assert len(lines) >= 5
    assert len(set(lines)) == len(lines)
    assert catalog.text("explain_footer").startswith("This summary describes your past results")


def test_unknown_text_key_is_empty_not_a_crash(catalog: MessageCatalog) -> None:
    assert catalog.text("no_such_key") == ""
    assert catalog.lines("no_such_key") == []
