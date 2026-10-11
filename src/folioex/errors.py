"""Typed issues and the plain-English message catalog.

Code never shows exception text to the investor. Layers raise ``FolioExError`` or return ``Issue`` objects;
the UI renders them through ``MessageCatalog`` (wording lives in ``messages.toml``).
"""

from __future__ import annotations

import logging
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

logger = logging.getLogger("folioex.errors")

_LAST_RESORT = "Something went wrong. The rest of your dashboard is fine. Try refreshing the page."


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class IssueKind(StrEnum):
    UNEXPECTED = "unexpected"
    CHART_FAILED = "chart_failed"
    EXPLAIN_FAILED = "explain_failed"
    FILE_NOT_CSV = "file_not_csv"
    TOO_MANY_FILES = "too_many_files"
    FILE_TOO_LARGE = "file_too_large"
    TOTAL_TOO_LARGE = "total_too_large"
    FILE_UNREADABLE = "file_unreadable"
    NO_TRANSACTIONS_SECTION = "no_transactions_section"
    MISSING_COLUMN = "missing_column"
    BAD_DATES = "bad_dates"
    MONTH_DAY_DATES = "month_day_dates"
    UNKNOWN_TYPE = "unknown_type"
    UNPARSEABLE_ROWS = "unparseable_rows"
    MASKED_ROWS = "masked_rows"
    NON_USD_ROWS = "non_usd_rows"
    SIGN_MISMATCH = "sign_mismatch"
    NET_MISMATCH = "net_mismatch"
    TEMPLATE_ROWS_SKIPPED = "template_rows_skipped"
    DUPLICATES_IGNORED = "duplicates_ignored"
    HISTORY_GAP = "history_gap"
    DIVIDEND_WITHOUT_SHARES = "dividend_without_shares"
    CASH_MISMATCH = "cash_mismatch"
    CASH_CONTINUITY = "cash_continuity"
    COVERAGE_GAP = "coverage_gap"
    SPLIT_APPLIED = "split_applied"
    SYMBOL_UNRESOLVED = "symbol_unresolved"
    MARKET_DATA_OFFLINE = "market_data_offline"
    MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
    TICKER_NOT_FOUND = "ticker_not_found"
    PROFILE_MISSING = "profile_missing"
    NOT_ENOUGH_HISTORY = "not_enough_history"
    NO_NET_DEPOSITS = "no_net_deposits"
    SHADOW_CAPPED = "shadow_capped"
    TRANSFER_COST_ESTIMATED = "transfer_cost_estimated"
    PROCESSING_FAILED = "processing_failed"
    COMPARE_LIMIT = "compare_limit"
    SAMPLE_DATA_MISSING = "sample_data_missing"
    COMPARE_TICKER_NOT_FOUND = "compare_ticker_not_found"
    COMPARE_UNAVAILABLE = "compare_unavailable"
    COMPARE_STARTS_LATER = "compare_starts_later"
    UNIVERSE_UNAVAILABLE = "universe_unavailable"


class _Keyed(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def context(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Issue:
    kind: IssueKind
    severity: Severity = Severity.WARNING
    context: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    severity: Severity = Severity.WARNING


class FolioExError(Exception):
    """An expected problem with a known, friendly explanation."""

    def __init__(self, kind: IssueKind, severity: Severity = Severity.BLOCKING, **context: Any) -> None:
        super().__init__(kind.value)
        self.issue = Issue(kind, severity, MappingProxyType(dict(context)))


class MessageCatalog:
    """Plain-English wording for issues, UI text snippets, and rotating lines."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._issues: dict[str, str] = {k: str(v) for k, v in data.get("issues", {}).items()}
        self._text: dict[str, str] = {k: str(v) for k, v in data.get("text", {}).items()}
        self._lines: dict[str, list[str]] = {k: [str(x) for x in v] for k, v in data.get("lines", {}).items()}
        self._insights: dict[str, str] = {k: str(v) for k, v in data.get("insights", {}).items()}
        self._disclaimer: list[tuple[str, str]] = [
            (str(item["title"]), str(item["body"])) for item in data.get("disclaimer", [])
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> MessageCatalog:
        if path is None:
            raw = resources.files("folioex").joinpath("messages.toml").read_text(encoding="utf-8")
        else:
            raw = path.read_text(encoding="utf-8")
        return cls(tomllib.loads(raw))

    def has(self, kind: IssueKind) -> bool:
        return kind.value in self._issues

    def issue_keys(self) -> list[str]:
        return list(self._issues)

    def placeholders(self, kind: IssueKind) -> list[tuple[str, str]]:
        template = self._issues.get(kind.value, "")
        return [(name, spec or "") for _, name, spec, _ in string.Formatter().parse(template) if name]

    def render(self, issue: Issue) -> UserMessage:
        template = self._issues.get(issue.kind.value)
        if template is None:
            logger.warning("No message for issue kind %s", issue.kind.value)
            return self._generic(issue.severity)
        try:
            text = template.format_map(_with_phrases(issue.context))
        except (KeyError, ValueError, IndexError, AttributeError):
            # Log the keys only: context values can contain the investor's own data.
            logger.warning(
                "Message for %s could not be filled (context keys: %s)",
                issue.kind.value,
                sorted(issue.context),
            )
            return self._generic(issue.severity)
        return UserMessage(text.strip(), issue.severity)

    def _generic(self, severity: Severity) -> UserMessage:
        return UserMessage(self._issues.get(IssueKind.UNEXPECTED.value, _LAST_RESORT).strip(), severity)

    def text(self, key: str) -> str:
        return self._text.get(key, "")

    def lines(self, key: str) -> list[str]:
        return list(self._lines.get(key, []))

    def disclaimer(self) -> list[tuple[str, str]]:
        return list(self._disclaimer)

    def insight_template(self, key: str) -> str:
        return self._insights.get(key, "")

    def insight(self, item: _Keyed) -> str:
        """Wording for an insight (anything with ``key`` and ``context``); empty if it can't be filled."""
        template = self._insights.get(item.key)
        if template is None:
            logger.warning("No wording for insight %s", item.key)
            return ""
        try:
            return template.format_map(dict(item.context)).strip()
        except (KeyError, ValueError, IndexError, AttributeError):
            logger.warning(
                "Insight %s could not be filled (context keys: %s)", item.key, sorted(item.context)
            )
            return ""


def _with_phrases(context: Mapping[str, Any]) -> dict[str, Any]:
    """Add "1 row" / "3 rows" style phrases so messages read naturally for any count."""
    values = dict(context)
    count = values.get("count")
    if isinstance(count, int):
        values.setdefault("count_rows", f"{count:,} {'row' if count == 1 else 'rows'}")
        values.setdefault("count_holdings", f"{count:,} {'holding' if count == 1 else 'holdings'}")
    return values


@lru_cache(maxsize=1)
def default_catalog() -> MessageCatalog:
    return MessageCatalog.load()
