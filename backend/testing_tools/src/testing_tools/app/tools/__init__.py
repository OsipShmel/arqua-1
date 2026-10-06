"""Tool runners: one per tool, picked by `Settings.tool_mode`."""

from collections.abc import Mapping

from testing_tools.api.schemas import ToolName
from testing_tools.app.settings import Settings, ToolMode
from testing_tools.app.tools.base import ToolContext, ToolError, ToolRunner
from testing_tools.app.tools.microcks import MicrocksRunner
from testing_tools.app.tools.mutation import MutationRunner
from testing_tools.app.tools.schemathesis import SchemathesisRunner
from testing_tools.app.tools.stub import StubRunner


def default_runners(settings: Settings) -> Mapping[ToolName, ToolRunner]:
    if settings.tool_mode == ToolMode.STUB:
        stub = StubRunner()
        return {tool: stub for tool in ToolName}
    return {
        ToolName.SCHEMATHESIS: SchemathesisRunner(),
        ToolName.MICROCKS: MicrocksRunner(),
        ToolName.MUTATION: MutationRunner(),
    }


__all__ = ["ToolContext", "ToolError", "ToolRunner", "default_runners"]
