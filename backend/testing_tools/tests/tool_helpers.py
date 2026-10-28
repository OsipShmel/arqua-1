"""Builds a ToolContext for calling a runner directly, and records what it reports."""

from pathlib import Path
from typing import Any
from uuid import uuid4

from testing_tools.api.schemas import ArtifactRef, Target
from testing_tools.app.contract import LoadedContract
from testing_tools.app.settings import Settings
from testing_tools.app.tools.base import ToolContext


class Collected:
    """What a runner reported to the engine through its ToolContext."""

    def __init__(self) -> None:
        self.artifacts: dict[str, tuple[str, bytes]] = {}
        self.progress: list[float] = []


def make_ctx(
    tmp_path: Path,
    contract: LoadedContract,
    base_url: str,
    settings: Settings | None = None,
    **target: Any,
) -> tuple[ToolContext, Collected]:
    collected = Collected()

    def add_artifact(name: str, media_type: str, content: bytes) -> ArtifactRef:
        collected.artifacts[name] = (media_type, content)
        return ArtifactRef(name=name, media_type=media_type, size_bytes=len(content), url=name)

    work_dir = tmp_path / "work"
    work_dir.mkdir(exist_ok=True)
    ctx = ToolContext(
        run_id=uuid4(),
        contract=contract,
        target=Target.model_validate({"base_url": base_url, **target}),
        work_dir=work_dir,
        settings=settings or Settings(tool_mode="real"),
        set_progress=collected.progress.append,
        add_artifact=add_artifact,
    )
    return ctx, collected
