"""pytest plugin loaded into the agent's test run (`-p arqa_report_plugin`).

Writes per-test outcomes with real node ids to $ARQA_REPORT_PATH as JSON, including the
exception type behind each failure (the gateway classifies failure causes from it).
Runs inside the test environment, so it must only use the standard library and pytest.
"""

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
_tests: dict[str, dict[str, Any]] = {}
_collection_errors: list[str] = []


def _message(report: Any) -> str | None:
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    text = getattr(crash, "message", None) or getattr(report, "longreprtext", "") or ""
    text = str(text).strip()
    return text[:500] or None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    if call.excinfo is not None and report.failed:
        # class names along the MRO: "ConnectError", "TransportError", ..., "Exception"
        report.arqa_exc_types = [cls.__name__ for cls in call.excinfo.type.__mro__]


def pytest_runtest_logreport(report: Any) -> None:
    record = _tests.setdefault(
        report.nodeid,
        {"outcome": "passed", "duration": 0.0, "message": None, "exc_types": [], "when": None},
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
        record["exc_types"] = getattr(report, "arqa_exc_types", [])
        record["when"] = report.when


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
