"""Background jobs and progress reporting (criteria P2, P3)."""

import threading
import time

import pytest

from folioex.errors import FolioExError, IssueKind
from folioex.jobs.runner import JobRegistry, Snapshot
from folioex.jobs.stages import STAGES
from folioex.progress import Progress


def run_all_stages(progress: Progress) -> str:
    for stage in STAGES:
        progress.stage(stage.key)
    return "ok"


def wait(reg: JobRegistry[str], job_id: str) -> Snapshot:
    snap = reg.wait(job_id, timeout=10)
    assert snap is not None
    return snap


def test_success_reaches_25_of_25() -> None:
    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    job = reg.start(run_all_stages)
    snap = wait(reg, job)
    assert snap.status == "done"
    assert snap.stage == 25
    assert snap.percent == 100
    assert snap.caption == "All 25 stages done · 100%"
    assert reg.result(job) == "ok"


def test_progress_is_monotonic_and_ordered() -> None:
    seen: list[int] = []
    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0, on_change=lambda s: seen.append(s.stage))
    wait(reg, reg.start(run_all_stages))
    assert seen == sorted(seen)
    assert seen[-1] == 25


def test_going_backwards_is_ignored() -> None:
    def work(progress: Progress) -> str:
        progress.stage("merge_files")
        progress.stage("read_files")  # out of order: must not move the bar back
        return "ok"

    seen: list[int] = []
    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0, on_change=lambda s: seen.append(s.stage))
    wait(reg, reg.start(work))
    assert seen == sorted(seen)


def test_failure_stops_at_failing_stage_with_friendly_issue() -> None:
    def work(progress: Progress) -> str:
        for stage in STAGES[:5]:
            progress.stage(stage.key)
        raise ValueError("boom")

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    snap = wait(reg, reg.start(work))
    assert snap.status == "failed"
    assert snap.stage == 4
    assert snap.issue is not None and snap.issue.kind is IssueKind.PROCESSING_FAILED


def test_known_problem_keeps_its_own_message() -> None:
    def work(progress: Progress) -> str:
        raise FolioExError(IssueKind.SAMPLE_DATA_MISSING)

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    snap = wait(reg, reg.start(work))
    assert snap.issue is not None and snap.issue.kind is IssueKind.SAMPLE_DATA_MISSING


def test_sub_progress_feeds_percent_and_caption() -> None:
    gate = threading.Event()

    def work(progress: Progress) -> str:
        for stage in STAGES[:10]:
            progress.stage(stage.key)
        progress.stage("fetch_prices")
        progress.sub(9, 16, "stocks")
        gate.wait(5)
        return "ok"

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    job = reg.start(work)
    deadline = time.monotonic() + 5
    while (snap := reg.snapshot(job)) is not None and snap.sub_done != 9 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert snap is not None
    assert snap.stage == 10
    assert snap.percent == int((10 + 9 / 16) / 25 * 100)
    assert snap.caption == f"Stage 11/25 · Fetch prices (9/16 stocks) · {snap.percent}%"
    gate.set()
    wait(reg, job)


def test_new_job_cancels_the_running_one() -> None:
    started = threading.Event()

    def slow(progress: Progress) -> str:
        started.set()
        for _ in range(500):
            progress.sub(0, 1)  # checkpoint: cancellation is noticed here
            time.sleep(0.01)
        return "slow"

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    first = reg.start(slow)
    started.wait(5)
    second = reg.start(run_all_stages)
    assert wait(reg, second).status == "done"
    first_snap = reg.snapshot(first)
    assert first_snap is not None and first_snap.status == "cancelled"
    assert reg.result(first) is None


def test_at_most_one_job_runs_even_under_concurrent_starts() -> None:
    running = 0
    peak = 0
    lock = threading.Lock()

    def work(progress: Progress) -> str:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            for _ in range(20):
                progress.sub(0, 1)
                time.sleep(0.002)
        finally:
            with lock:
                running -= 1
        return "w"

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    ids: list[str] = []
    threads = [threading.Thread(target=lambda: ids.append(reg.start(work))) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = [wait(reg, i).status for i in ids]
    assert peak == 1
    assert statuses.count("done") >= 1
    assert set(statuses) <= {"done", "cancelled"}


def test_minimum_stage_time_makes_the_animation_visible() -> None:
    reg: JobRegistry[str] = JobRegistry(min_stage_ms=20)
    begin = time.monotonic()
    wait(reg, reg.start(run_all_stages))
    assert time.monotonic() - begin >= 24 * 0.02 * 0.9


def test_retry_runs_the_last_work_again() -> None:
    calls = 0

    def work(progress: Progress) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("first time fails")
        return "second time works"

    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    assert wait(reg, reg.start(work)).status == "failed"
    again = reg.retry()
    assert again is not None
    assert wait(reg, again).status == "done"
    assert reg.result(again) == "second time works"


def test_unknown_job_is_none() -> None:
    reg: JobRegistry[str] = JobRegistry(min_stage_ms=0)
    assert reg.snapshot("nope") is None
    assert reg.snapshot(None) is None
    assert reg.retry() is None


@pytest.mark.parametrize("bad", [-1, 26])
def test_snapshot_percent_is_clamped(bad: int) -> None:
    snap = Snapshot("x", "running", bad, "", 0, 0, "", None)
    assert 0 <= snap.percent <= 100
