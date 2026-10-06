import asyncio
import logging
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

import httpx2

from testing_tools.api.schemas import (
    ArtifactRef,
    ErrorCode,
    ErrorInfo,
    RunState,
    ToolName,
    ToolResult,
    ToolState,
)
from testing_tools.app.settings import Settings, ToolMode
from testing_tools.app.store import RESULT_TYPES, Artifact, RunRecord, ToolRun, utcnow
from testing_tools.app.tools import ToolContext, ToolError, ToolRunner, default_runners

PROBE_ATTEMPTS = 3
PROBE_PAUSE_S = 1.0

log = logging.getLogger(__name__)


class TargetUnreachable(Exception):
    pass


class Engine:
    """Walks a run through its lifecycle, handing each tool to its runner."""

    def __init__(
        self, settings: Settings, runners: Mapping[ToolName, ToolRunner] | None = None
    ) -> None:
        self._settings = settings
        self._runners = runners if runners is not None else default_runners(settings)
        self._probe_target = settings.tool_mode == ToolMode.REAL

    def start(self, record: RunRecord) -> None:
        record.task = asyncio.create_task(self.run(record, record.request.timeout_s))

    def cancel(self, record: RunRecord) -> None:
        # A task cancelled before its first step never runs its own cleanup.
        if record.state == RunState.QUEUED:
            _finish_cancelled(record)
        if record.task is not None:
            record.task.cancel()

    async def run(self, record: RunRecord, timeout_s: float) -> None:
        record.state = RunState.RUNNING
        record.started_at = utcnow()
        log.info("Run %s started", record.run_id)
        try:
            async with asyncio.timeout(timeout_s):
                if self._probe_target:
                    await _probe(record)
                for tool in record.tools:
                    await self._run_tool(record, tool)
        except TimeoutError:
            error = ErrorInfo(code=ErrorCode.TIMEOUT, message=f"run exceeded {timeout_s}s")
            _interrupt_tools(record, ToolState.ERROR, error)
            _finish(record, RunState.TIMED_OUT, error)
        except TargetUnreachable as exc:
            error = ErrorInfo(code=ErrorCode.TARGET_UNREACHABLE, message=str(exc))
            _interrupt_tools(record, ToolState.SKIPPED)
            _finish(record, RunState.FAILED, error)
        except asyncio.CancelledError:
            _finish_cancelled(record)
            raise
        else:
            _finish(record, RunState.COMPLETED)

    async def _run_tool(self, record: RunRecord, tool: ToolRun) -> None:
        tool.state = ToolState.RUNNING
        tool.started_at = utcnow()
        tool.progress = 0.0
        refs: list[ArtifactRef] = []

        def set_progress(value: float) -> None:
            tool.progress = round(min(max(value, 0.0), 1.0), 4)

        def add_artifact(name: str, media_type: str, content: bytes) -> ArtifactRef:
            record.artifacts[name] = Artifact(media_type=media_type, content=content)
            ref = ArtifactRef(
                name=name,
                media_type=media_type,
                size_bytes=len(content),
                url=f"{record.links.self}/artifacts/{name}",
            )
            refs[:] = [r for r in refs if r.name != name] + [ref]
            return ref

        name = tool.config.tool
        work_dir = Path(
            tempfile.mkdtemp(prefix=f"tt-{name.value}-", dir=self._settings.work_dir or None)
        )
        ctx = ToolContext(
            run_id=record.run_id,
            contract=record.contract,
            target=record.request.target,
            work_dir=work_dir,
            settings=self._settings,
            set_progress=set_progress,
            add_artifact=add_artifact,
        )
        log.info("Run %s: %s started", record.run_id, name.value)
        try:
            result = await self._runners[name].run(tool.config, ctx)
        except ToolError as exc:
            log.warning("Run %s: %s failed: %s", record.run_id, name.value, exc.message)
            result = _error_result(tool, exc.info())
        except Exception as exc:
            log.exception("Run %s: %s crashed", record.run_id, name.value)
            info = ErrorInfo(code=ErrorCode.TOOL_FAILED, message=f"{type(exc).__name__}: {exc}")
            result = _error_result(tool, info)
        finally:
            if not self._settings.keep_work_dirs:
                shutil.rmtree(work_dir, ignore_errors=True)

        tool.finished_at = utcnow()
        tool.result = result.model_copy(
            update={
                "started_at": tool.started_at,
                "finished_at": tool.finished_at,
                "duration_s": (tool.finished_at - tool.started_at).total_seconds(),
                "artifacts": refs,
            }
        )
        tool.state = result.state
        tool.error = result.error
        tool.progress = 1.0
        log.info("Run %s: %s finished: %s", record.run_id, name.value, tool.state.value)


async def _probe(record: RunRecord) -> None:
    """Any HTTP answer from the target is fine; only a dead socket fails the run."""
    target = record.request.target
    url = str(target.base_url)
    async with httpx2.AsyncClient(
        verify=target.tls_verify, timeout=target.request_timeout_s
    ) as client:
        for attempt in range(1, PROBE_ATTEMPTS + 1):
            try:
                await client.get(url, headers=target.headers)
                return
            except httpx2.HTTPError as exc:
                if attempt == PROBE_ATTEMPTS:
                    raise TargetUnreachable(f"{url}: {exc!r}") from exc
                await asyncio.sleep(PROBE_PAUSE_S)


def _error_result(tool: ToolRun, error: ErrorInfo) -> ToolResult:
    return RESULT_TYPES[tool.config.tool](state=ToolState.ERROR, error=error)


def _interrupt_tools(
    record: RunRecord, running_state: ToolState, error: ErrorInfo | None = None
) -> None:
    now = utcnow()
    for tool in record.tools:
        if tool.state == ToolState.RUNNING:
            tool.state = running_state
            tool.error = error
            tool.finished_at = now
        elif tool.state == ToolState.PENDING:
            tool.state = ToolState.SKIPPED


def _finish_cancelled(record: RunRecord) -> None:
    _interrupt_tools(record, ToolState.CANCELLED)
    _finish(record, RunState.CANCELLED)


def _finish(record: RunRecord, state: RunState, error: ErrorInfo | None = None) -> None:
    record.state = state
    record.error = error
    record.finished_at = utcnow()
    log.info("Run %s finished: %s", record.run_id, state.value)
