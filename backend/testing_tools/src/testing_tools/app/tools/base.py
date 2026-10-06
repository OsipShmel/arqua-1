"""The contract between the engine and a tool runner."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from testing_tools.api.schemas import (
    ArtifactRef,
    ErrorCode,
    ErrorInfo,
    Target,
    ToolConfig,
    ToolResult,
)
from testing_tools.app.contract import LoadedContract
from testing_tools.app.settings import Settings


@dataclass
class ToolContext:
    run_id: UUID
    contract: LoadedContract
    target: Target
    work_dir: Path
    """Scratch directory owned by this tool; removed after the tool finishes."""
    settings: Settings
    set_progress: Callable[[float], None]
    add_artifact: Callable[[str, str, bytes], ArtifactRef]
    """(name, media_type, content) -> ref; the engine attaches refs to the result."""

    @property
    def base_url(self) -> str:
        """Target URL without the trailing slash pydantic adds to bare hosts."""
        return str(self.target.base_url).rstrip("/")


class ToolError(Exception):
    """The tool could not do its job; the engine turns this into `state=error`."""

    def __init__(
        self, code: ErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, details=self.details)


class ToolRunner(Protocol):
    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        """Run the tool and return its result.

        The engine fills `started_at`, `finished_at`, `duration_s` and `artifacts`.
        Must release processes/servers when cancelled (CancelledError propagates).
        """
        ...
