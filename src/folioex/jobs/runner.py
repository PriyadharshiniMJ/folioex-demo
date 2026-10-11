"""Run one piece of work at a time in a background thread and report its progress.

Single user: starting a new job cancels the running one (cooperatively, at its next progress checkpoint)
and waits for it to stop, so at most one job ever runs. State lives in memory only.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from folioex.errors import FolioExError, Issue, IssueKind, Severity
from folioex.jobs.stages import STAGES, stage_index

logger = logging.getLogger("folioex.jobs")

Status = Literal["running", "done", "failed", "cancelled"]
TOTAL = len(STAGES)
_KEEP = 32


class JobCancelled(Exception):  # control flow: a newer job replaced this one
    pass


@dataclass(frozen=True, slots=True)
class Snapshot:
    job_id: str
    status: Status
    stage: int  # index of the current stage; TOTAL once finished
    label: str
    sub_done: int
    sub_total: int
    unit: str
    issue: Issue | None

    @property
    def percent(self) -> int:
        if self.status == "done":
            return 100
        fraction = self.sub_done / self.sub_total if self.sub_total else 0.0
        return max(0, min(100, int((self.stage + fraction) / TOTAL * 100)))

    @property
    def caption(self) -> str:
        if self.status == "done":
            return f"All {TOTAL} stages done · 100%"
        detail = (
            f" ({self.sub_done}/{self.sub_total} {self.unit})".replace(" )", ")") if self.sub_total else ""
        )
        return f"Stage {min(self.stage, TOTAL - 1) + 1}/{TOTAL} · {self.label}{detail} · {self.percent}%"


class _Job[T]:
    def __init__(self, job_id: str, work: Callable[[_Reporter[T]], T]) -> None:
        self.id = job_id
        self.work = work
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.snapshot = Snapshot(job_id, "running", 0, STAGES[0].label, 0, 0, "", None)
        self.result: T | None = None
        self.stage_started = time.monotonic()
        self.thread: threading.Thread | None = None


class _Reporter[T]:
    """The ``Progress`` handed to work: updates the snapshot and is the cancellation checkpoint."""

    def __init__(self, job: _Job[T], registry: JobRegistry[T]) -> None:
        self._job = job
        self._registry = registry

    def stage(self, key: str) -> None:
        self._checkpoint()
        index = stage_index(key)
        job = self._job
        if index <= job.snapshot.stage and job.snapshot.label == STAGES[index].label:
            return
        if index < job.snapshot.stage:
            return  # never move the bar backwards
        self._registry.hold(job)
        self._registry.update(job, stage=index, label=STAGES[index].label, sub_done=0, sub_total=0, unit="")

    def sub(self, done: int, total: int, unit: str = "") -> None:
        self._checkpoint()
        self._registry.update(self._job, sub_done=done, sub_total=total, unit=unit)

    def _checkpoint(self) -> None:
        if self._job.cancel.is_set():
            raise JobCancelled


class JobRegistry[T]:
    def __init__(
        self,
        *,
        min_stage_ms: int = 80,
        on_change: Callable[[Snapshot], None] | None = None,
    ) -> None:
        self._min_stage_s = min_stage_ms / 1000
        self._on_change = on_change
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()  # serializes starts so cancellation is orderly
        self._jobs: dict[str, _Job[T]] = {}
        self._current: _Job[T] | None = None
        self._last_work: Callable[[_Reporter[T]], T] | None = None

    # ---- public API ----

    def configure(self, *, min_stage_ms: int) -> None:
        self._min_stage_s = min_stage_ms / 1000

    def start(self, work: Callable[[_Reporter[T]], T]) -> str:
        with self._start_lock:
            previous = self._current
            if previous is not None:
                previous.cancel.set()
                if previous.thread is not None:
                    previous.thread.join(timeout=30)
            job = _Job(secrets.token_hex(8), work)
            with self._lock:
                self._jobs[job.id] = job
                for stale in list(self._jobs)[:-_KEEP]:  # remember only recent jobs
                    del self._jobs[stale]
                self._current = job
                self._last_work = work
            job.thread = threading.Thread(
                target=self._run, args=(job,), daemon=True, name=f"folioex-job-{job.id}"
            )
            job.thread.start()
            return job.id

    def retry(self) -> str | None:
        work = self._last_work
        return None if work is None else self.start(work)

    def cancel(self) -> None:
        current = self._current
        if current is not None:
            current.cancel.set()

    def snapshot(self, job_id: str | None) -> Snapshot | None:
        if not job_id:
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.snapshot

    def result(self, job_id: str | None) -> T | None:
        with self._lock:
            job = self._jobs.get(job_id or "")
            if job is None or job.snapshot.status != "done":
                return None
            return job.result

    def wait(self, job_id: str, timeout: float) -> Snapshot | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        job.finished.wait(timeout)
        return self.snapshot(job_id)

    # ---- used by the reporter and the worker thread ----

    def hold(self, job: _Job[T]) -> None:
        """Keep each stage on screen for a moment so the animation is visible but never slow."""
        remaining = self._min_stage_s - (time.monotonic() - job.stage_started)
        if remaining > 0:
            job.cancel.wait(remaining)
        job.stage_started = time.monotonic()

    def update(self, job: _Job[T], **changes: object) -> None:
        with job.lock:
            job.snapshot = replace(job.snapshot, **changes)  # type: ignore[arg-type]
            snap = job.snapshot
        if self._on_change is not None:
            self._on_change(snap)

    def _run(self, job: _Job[T]) -> None:
        reporter = _Reporter(job, self)
        try:
            result = job.work(reporter)
            if job.cancel.is_set():
                raise JobCancelled
            self.hold(job)
            job.result = result
            self.update(job, status="done", stage=TOTAL, label="Done", sub_done=0, sub_total=0, unit="")
        except JobCancelled:
            self.update(job, status="cancelled")
        except FolioExError as exc:
            logger.warning("Processing stopped: %s", exc.issue.kind.value)
            self.update(job, status="failed", issue=exc.issue)
        except Exception:
            logger.exception("Processing failed at stage %s", job.snapshot.label)
            self.update(job, status="failed", issue=Issue(IssueKind.PROCESSING_FAILED, Severity.BLOCKING))
        finally:
            job.finished.set()
