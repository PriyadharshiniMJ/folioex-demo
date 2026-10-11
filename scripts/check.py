"""Run every quality gate and print a pass/fail table. Exit code 0 only when all gates pass.

Usage:  uv run python scripts/check.py           (skips browser tests)
        uv run python scripts/check.py --e2e     (also runs Playwright browser tests)
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_JSON = ROOT / ".coverage.json"
CORE_TARGET = 90.0  # Q5: ingestion/, analytics/, jobs/, errors.py
OVERALL_TARGET = 70.0
CORE_PREFIXES = ("folioex/ingestion/", "folioex/analytics/", "folioex/jobs/", "folioex/errors.py")


@dataclass
class Result:
    gate: str
    ok: bool
    detail: str = ""
    output: str = ""


def run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        cmd, check=False, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1][:120] if lines else ""


def coverage_results() -> list[Result]:
    data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    core_hit = core_total = all_hit = all_total = 0
    for name, info in data["files"].items():
        s = info["summary"]
        hit = s["covered_lines"] + s.get("covered_branches", 0)
        total = s["num_statements"] + s.get("num_branches", 0)
        all_hit, all_total = all_hit + hit, all_total + total
        posix = name.replace("\\", "/")
        if any(p in posix for p in CORE_PREFIXES):
            core_hit, core_total = core_hit + hit, core_total + total
    core = 100.0 * core_hit / core_total if core_total else 100.0
    overall = 100.0 * all_hit / all_total if all_total else 100.0
    return [
        Result("Q5 core coverage >= 90%", core >= CORE_TARGET, f"{core:.1f}% line+branch"),
        Result("Q5 overall coverage >= 70%", overall >= OVERALL_TARGET, f"{overall:.1f}% line+branch"),
    ]


def main() -> int:
    py = sys.executable
    results: list[Result] = []

    ok, out = run([py, "-m", "ruff", "check", "."])
    results.append(Result("Q1 lint (ruff check)", ok, last_line(out), out))
    ok, out = run([py, "-m", "ruff", "format", "--check", "."])
    results.append(Result("Q2 format (ruff format)", ok, last_line(out), out))
    ok, out = run([py, "-m", "mypy"])
    results.append(Result("Q3 types (mypy --strict)", ok, last_line(out), out))

    COVERAGE_JSON.unlink(missing_ok=True)
    ok, out = run(
        [
            py,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "not live and not e2e",
            "--cov",
            f"--cov-report=json:{COVERAGE_JSON.name}",
        ]
    )
    results.append(Result("Q4/Q6-Q8 tests (no network)", ok, last_line(out), out))
    if COVERAGE_JSON.exists():
        results.extend(coverage_results())
    else:
        results.append(Result("Q5 coverage", False, "no coverage report produced"))

    if "--e2e" in sys.argv:
        ok, out = run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "e2e"])
        results.append(Result("U* browser tests (Playwright)", ok, last_line(out), out))

    width = max(len(r.gate) for r in results)
    print()
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.gate.ljust(width)}  {r.detail}")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} gates passed")
    for r in failed:
        if r.output:
            print(f"\n--- {r.gate} ---\n{r.output[-4000:]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
