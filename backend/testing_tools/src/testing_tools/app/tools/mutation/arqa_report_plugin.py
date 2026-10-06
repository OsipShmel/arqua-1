"""pytest plugin loaded into the agent's test run (`-p arqa_report_plugin`).

Writes per-test outcomes with real node ids to $ARQA_REPORT_PATH as JSON.
Runs inside the test environment, so it must only use the standard library and pytest.
"""

import json
import os
from typing import Any

_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
_tests: dict[str, dict[str, Any]] = {}
_collection_errors: list[str] = []


def _message(report: Any) -> str | None:
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    text = getattr(crash, "message", None) or getattr(report, "longreprtext", "") or ""
    text = str(text).strip()
    return text[:500] or None


def pytest_runtest_logreport(report: Any) -> None:
    record = _tests.setdefault(
        report.nodeid, {"outcome": "passed", "duration": 0.0, "message": None}
    )
    record["duration"] += report.duration
    if hasattr(report, "wasxfail") or report.skipped:
        outcome = "skipped"
    elif report.failed:
        outcome = "failed" if report.when == "call" else "error"
    else:
        return
    if _RANK[outcome] > _RANK[record["outcome"]]:
        record["outcome"] = outcome
        record["message"] = _message(report) if outcome != "skipped" else None


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _collection_errors.append(report.nodeid or str(getattr(report, "fspath", "?")))


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    path = os.environ.get("ARQA_REPORT_PATH")
    if not path:
        return
    with open(path, "w") as out:
        json.dump(
            {
                "exitstatus": int(exitstatus),
                "tests": _tests,
                "collection_errors": _collection_errors,
            },
            out,
        )
