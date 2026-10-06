"""Engine with real-mode wiring: runner errors, crashes, target probe, scratch dirs, processes."""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from petstore import PETSTORE_JSON, inline_contract
from testing_tools.api.schemas import (
    ErrorCode,
    RunCreateRequest,
    RunState,
    SchemathesisResult,
    ToolConfig,
    ToolName,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import parse_contract
from testing_tools.app.engine import Engine
from testing_tools.app.settings import Settings, ToolMode
from testing_tools.app.store import RunRecord
from testing_tools.app.tools import ToolContext, ToolError, ToolRunner
from testing_tools.app.tools.process import run_process

pytestmark = pytest.mark.anyio


def make_record(base_url: str, *tools: dict[str, Any]) -> RunRecord:
    request = RunCreateRequest.model_validate(
        {"contract": inline_contract(), "target": {"base_url": base_url}, "tools": list(tools)}
    )
    return RunRecord.new(request, parse_contract(PETSTORE_JSON), "/api/v0")


class Scripted:
    """A runner whose behaviour the test decides."""

    def __init__(self, outcome: ToolResult | Exception) -> None:
        self.outcome = outcome
        self.work_dir: Path | None = None

    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        self.work_dir = ctx.work_dir
        (ctx.work_dir / "scratch.txt").write_text("x")
        ctx.add_artifact("log.txt", "text/plain", b"hello")
        ctx.set_progress(2.0)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def engine(settings: Settings, **runners: ToolRunner) -> Engine:
    return Engine(settings, {ToolName(name): runner for name, runner in runners.items()})


REAL = Settings(tool_mode=ToolMode.REAL)


async def test_tool_error_and_crash_do_not_stop_the_run(
    tmp_path: Path, petstore_url: str
) -> None:
    failing = Scripted(ToolError(ErrorCode.TOOL_FAILED, "nope", {"why": 1}))
    crashing = Scripted(RuntimeError("bug"))
    fine = Scripted(SchemathesisResult(state=ToolState.PASSED))
    settings = REAL.model_copy(update={"work_dir": str(tmp_path)})
    record = make_record(
        petstore_url,
        {"tool": "microcks"},
        {"tool": "mutation", "tests": {"kind": "inline", "files": {"t.py": ""}}},
        {"tool": "schemathesis"},
    )

    await engine(settings, microcks=failing, mutation=crashing, schemathesis=fine).run(
        record, timeout_s=10
    )

    assert record.state == RunState.COMPLETED
    microcks, mutation, schemathesis = record.tools
    assert microcks.state == ToolState.ERROR
    assert microcks.error is not None and microcks.error.details == {"why": 1}
    assert mutation.state == ToolState.ERROR
    assert mutation.error is not None
    assert (mutation.error.code, mutation.error.message) == (
        ErrorCode.TOOL_FAILED,
        "RuntimeError: bug",
    )
    assert schemathesis.state == ToolState.PASSED
    result = schemathesis.result
    assert result is not None and result.duration_s is not None
    assert [a.name for a in result.artifacts] == ["log.txt"]
    assert record.artifacts["log.txt"].content == b"hello"
    assert all(t.progress == 1.0 for t in record.tools)
    assert fine.work_dir is not None and fine.work_dir.parent == tmp_path
    assert not fine.work_dir.exists()


async def test_keep_work_dirs(tmp_path: Path, petstore_url: str) -> None:
    runner = Scripted(SchemathesisResult(state=ToolState.PASSED))
    settings = REAL.model_copy(update={"work_dir": str(tmp_path), "keep_work_dirs": True})

    await engine(settings, schemathesis=runner).run(
        make_record(petstore_url, {"tool": "schemathesis"}), timeout_s=10
    )

    assert runner.work_dir is not None and (runner.work_dir / "scratch.txt").exists()


async def test_unreachable_target_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("testing_tools.app.engine.PROBE_PAUSE_S", 0)
    runner = Scripted(SchemathesisResult(state=ToolState.PASSED))
    record = make_record("http://127.0.0.1:9", {"tool": "schemathesis"})

    await engine(REAL, schemathesis=runner).run(record, timeout_s=10)

    assert record.state == RunState.FAILED
    assert record.error is not None and record.error.code == ErrorCode.TARGET_UNREACHABLE
    assert [t.state for t in record.tools] == [ToolState.SKIPPED]
    assert runner.work_dir is None


async def test_stub_mode_does_not_probe() -> None:
    record = make_record("http://127.0.0.1:9", {"tool": "schemathesis"})

    await Engine(Settings(tool_mode=ToolMode.STUB, tool_delay_s=0)).run(record, timeout_s=10)

    assert record.state == RunState.COMPLETED


async def test_process_timeout_kills_the_group(tmp_path: Path) -> None:
    script = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); print('up', flush=True); time.sleep(60)"  # noqa: E501
    started = time.monotonic()

    result = await run_process([sys.executable, "-c", script], cwd=tmp_path, timeout_s=1)

    assert result.timed_out
    assert result.output.startswith(b"up")
    assert time.monotonic() - started < 10


async def test_process_cancel_kills(tmp_path: Path) -> None:
    marker = tmp_path / "alive"
    script = f"import time, pathlib\nwhile True:\n    pathlib.Path({str(marker)!r}).touch()\n    time.sleep(0.05)"
    task = asyncio.create_task(run_process([sys.executable, "-c", script], cwd=tmp_path))
    while not marker.exists():
        await asyncio.sleep(0.05)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    marker.unlink()
    await asyncio.sleep(0.3)

    assert not marker.exists()


async def test_process_result(tmp_path: Path) -> None:
    result = await run_process(
        [sys.executable, "-c", "import sys; print('out'); sys.exit(3)"], cwd=tmp_path
    )

    assert (result.returncode, result.timed_out) == (3, False)
    assert result.tail().strip() == "out"
