"""How long-running work reports progress, without knowing who is listening."""

from __future__ import annotations

from typing import Protocol


class Progress(Protocol):
    def stage(self, key: str) -> None:
        """Start the named stage (see ``jobs.stages``). Stages only move forward."""

    def sub(self, done: int, total: int, unit: str = "") -> None:
        """Progress within the current stage, e.g. ``sub(9, 16, "stocks")``. Also a cancel checkpoint."""


class NullProgress:
    """Progress sink for callers that don't show a progress bar."""

    def stage(self, key: str) -> None:
        return None

    def sub(self, done: int, total: int, unit: str = "") -> None:
        return None
