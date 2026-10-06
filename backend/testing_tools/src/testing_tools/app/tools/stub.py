"""Fake runner: waits `tool_delay_s` and returns a result derived from the contract."""

import asyncio

from testing_tools.api.schemas import ToolConfig, ToolResult
from testing_tools.app.store import utcnow
from testing_tools.app.stub_results import build_result
from testing_tools.app.tools.base import ToolContext

PROGRESS_STEPS = 10


class StubRunner:
    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        started_at = utcnow()
        for step in range(1, PROGRESS_STEPS + 1):
            await asyncio.sleep(ctx.settings.tool_delay_s / PROGRESS_STEPS)
            ctx.set_progress(step / PROGRESS_STEPS)
        result = build_result(
            config, ctx.contract, ctx.settings.version, started_at, utcnow()
        )
        ctx.add_artifact(
            f"{config.tool.value}-stub.json",
            "application/json",
            result.model_dump_json(indent=2).encode(),
        )
        return result
