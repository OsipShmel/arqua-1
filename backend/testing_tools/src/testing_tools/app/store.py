import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from testing_tools.api.schemas import (
    TERMINAL_RUN_STATES,
    ArtifactRef,
    ErrorInfo,
    MicrocksResult,
    MutationResult,
    RunCreated,
    RunCreateRequest,
    RunLinks,
    RunResult,
    RunState,
    RunStatus,
    SchemathesisResult,
    ToolConfig,
    ToolName,
    ToolProgress,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import LoadedContract
from testing_tools.app.metrics import run_metrics

REPORT_ARTIFACT = "run-report.md"

RESULT_TYPES: dict[ToolName, type[SchemathesisResult | MicrocksResult | MutationResult]] = {
    ToolName.SCHEMATHESIS: SchemathesisResult,
    ToolName.MICROCKS: MicrocksResult,
    ToolName.MUTATION: MutationResult,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Artifact:
    media_type: str
    content: bytes


@dataclass
class ToolRun:
    config: ToolConfig
    state: ToolState = ToolState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float | None = None
    error: ErrorInfo | None = None
    result: ToolResult | None = None

    def progress_view(self) -> ToolProgress:
        return ToolProgress(
            tool=self.config.tool,
            state=self.state,
            started_at=self.started_at,
            finished_at=self.finished_at,
            progress=self.progress,
            error=self.error,
        )

    def result_view(self) -> ToolResult:
        if self.result is not None:
            return self.result
        # tool never produced a result: cancelled, skipped or interrupted
        return RESULT_TYPES[self.config.tool](
            state=self.state,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
        )


@dataclass
class RunRecord:
    request: RunCreateRequest
    contract: LoadedContract
    links: RunLinks
    run_id: UUID
    created_at: datetime = field(default_factory=utcnow)
    state: RunState = RunState.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ErrorInfo | None = None
    tools: list[ToolRun] = field(default_factory=list)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    archived_result: RunResult | None = None
    """Set for runs read back from disk: their result is served as saved."""

    @classmethod
    def new(
        cls, request: RunCreateRequest, contract: LoadedContract, api_prefix: str
    ) -> "RunRecord":
        run_id = uuid4()
        base = f"{api_prefix}/runs/{run_id}"
        return cls(
            request=request,
            contract=contract,
            links=RunLinks(self=base, result=f"{base}/result", cancel=f"{base}/cancel"),
            run_id=run_id,
            tools=[ToolRun(config=config) for config in request.tools],
        )

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_RUN_STATES

    def created_view(self) -> RunCreated:
        return RunCreated(
            run_id=self.run_id, state=RunState.QUEUED, created_at=self.created_at, links=self.links
        )

    def status_view(self) -> RunStatus:
        return RunStatus(
            run_id=self.run_id,
            state=self.state,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            tools=[tool.progress_view() for tool in self.tools],
            labels=self.request.labels,
            error=self.error,
            links=self.links,
        )

    def result_view(self) -> RunResult:
        if self.archived_result is not None:
            return self.archived_result
        results = [tool.result_view() for tool in self.tools]
        report = self.artifacts.get(REPORT_ARTIFACT)
        duration = (
            (self.finished_at - self.started_at).total_seconds()
            if self.started_at and self.finished_at
            else None
        )
        return RunResult(
            run_id=self.run_id,
            state=self.state,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_s=duration,
            labels=self.request.labels,
            contract=self.contract.info,
            target_base_url=str(self.request.target.base_url),
            tools=results,
            metrics=run_metrics(self.contract, results),
            report=ArtifactRef(
                name=REPORT_ARTIFACT,
                media_type=report.media_type,
                size_bytes=len(report.content),
                url=f"{self.links.self}/artifacts/{REPORT_ARTIFACT}",
            )
            if report
            else None,
            error=self.error,
        )


class RunStore:
    """In-memory run registry.

    Keeps at most `max_finished` finished runs in memory (oldest go first); `fallback`
    (the on-disk archive) is asked for runs that aren't in memory.
    """

    def __init__(
        self,
        fallback: Callable[[UUID], RunRecord | None] | None = None,
        max_finished: int | None = None,
    ) -> None:
        self._runs: dict[UUID, RunRecord] = {}
        self._idempotency: dict[str, UUID] = {}
        self._fallback = fallback
        self._max_finished = max_finished

    def add(self, record: RunRecord, idempotency_key: str | None = None) -> None:
        self._evict()
        self._runs[record.run_id] = record
        if idempotency_key is not None:
            self._idempotency[idempotency_key] = record.run_id

    def get(self, run_id: str | UUID) -> RunRecord | None:
        try:
            uuid = UUID(str(run_id))
        except ValueError:
            return None
        record = self._runs.get(uuid)
        if record is None and self._fallback is not None:
            record = self._fallback(uuid)
        return record

    def _evict(self) -> None:
        if self._max_finished is None:
            return
        finished = [r for r in self._runs.values() if r.finished]
        finished.sort(key=lambda r: r.finished_at or r.created_at)
        for record in finished[: max(0, len(finished) - self._max_finished + 1)]:
            del self._runs[record.run_id]
        live = set(self._runs)
        self._idempotency = {k: v for k, v in self._idempotency.items() if v in live}

    def by_idempotency_key(self, key: str) -> RunRecord | None:
        run_id = self._idempotency.get(key)
        return self._runs.get(run_id) if run_id else None

    def active_count(self) -> int:
        return sum(not record.finished for record in self._runs.values())

    def all(self) -> list[RunRecord]:
        return list(self._runs.values())
