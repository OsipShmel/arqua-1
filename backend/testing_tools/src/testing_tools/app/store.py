import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from testing_tools.api.schemas import (
    TERMINAL_RUN_STATES,
    ErrorInfo,
    RunCreated,
    RunCreateRequest,
    RunLinks,
    RunState,
    RunStatus,
    ToolConfig,
    ToolProgress,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import LoadedContract


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

    @classmethod
    def new(cls, request: RunCreateRequest, contract: LoadedContract, api_prefix: str) -> "RunRecord":
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


class RunStore:
    """In-memory run registry; lives as long as the process."""

    def __init__(self) -> None:
        self._runs: dict[UUID, RunRecord] = {}
        self._idempotency: dict[str, UUID] = {}

    def add(self, record: RunRecord, idempotency_key: str | None = None) -> None:
        self._runs[record.run_id] = record
        if idempotency_key is not None:
            self._idempotency[idempotency_key] = record.run_id

    def get(self, run_id: str | UUID) -> RunRecord | None:
        try:
            return self._runs.get(UUID(str(run_id)))
        except ValueError:
            return None

    def by_idempotency_key(self, key: str) -> RunRecord | None:
        run_id = self._idempotency.get(key)
        return self._runs.get(run_id) if run_id else None

    def active_count(self) -> int:
        return sum(not record.finished for record in self._runs.values())

    def all(self) -> list[RunRecord]:
        return list(self._runs.values())
