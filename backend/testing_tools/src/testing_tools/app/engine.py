import asyncio
import logging

from testing_tools.api.schemas import ArtifactRef, ErrorCode, ErrorInfo, RunState, ToolState
from testing_tools.app.settings import Settings
from testing_tools.app.store import Artifact, RunRecord, ToolRun, utcnow
from testing_tools.app.stub_results import build_result

PROGRESS_STEPS = 10

log = logging.getLogger(__name__)


class StubEngine:
    """Walks a run through its lifecycle, faking each tool with a delay and a stub result."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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
                for tool in record.tools:
                    await self._run_tool(record, tool)
        except TimeoutError:
            error = ErrorInfo(code=ErrorCode.TIMEOUT, message=f"run exceeded {timeout_s}s")
            _interrupt_tools(record, ToolState.ERROR, error)
            _finish(record, RunState.TIMED_OUT, error)
        except asyncio.CancelledError:
            _finish_cancelled(record)
            raise
        else:
            _finish(record, RunState.COMPLETED)

    async def _run_tool(self, record: RunRecord, tool: ToolRun) -> None:
        tool.state = ToolState.RUNNING
        tool.started_at = utcnow()
        tool.progress = 0.0
        for step in range(1, PROGRESS_STEPS + 1):
            await asyncio.sleep(self._settings.tool_delay_s / PROGRESS_STEPS)
            tool.progress = step / PROGRESS_STEPS
        tool.finished_at = utcnow()

        result = build_result(
            tool.config, record.contract, self._settings.version, tool.started_at, tool.finished_at
        )
        name = f"{tool.config.tool.value}-stub.json"
        content = result.model_dump_json(indent=2).encode()
        record.artifacts[name] = Artifact(media_type="application/json", content=content)
        ref = ArtifactRef(
            name=name,
            media_type="application/json",
            size_bytes=len(content),
            url=f"{record.links.self}/artifacts/{name}",
        )
        tool.result = result.model_copy(update={"artifacts": [ref]})
        tool.state = result.state


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
