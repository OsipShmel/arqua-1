import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from testing_tools.api.schemas import ToolName

VERSION = "0.0.1"


@dataclass(frozen=True)
class Settings:
    api_prefix: str = "/api/v0"
    version: str = VERSION
    tool_delay_s: float = 1.0
    """Simulated duration of each stub tool."""
    max_active_runs: int = 16
    unavailable_tools: frozenset[ToolName] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        defaults = cls()
        unavailable = env.get("TT_UNAVAILABLE_TOOLS", "")
        return cls(
            api_prefix=env.get("TT_API_PREFIX", defaults.api_prefix),
            tool_delay_s=float(env.get("TT_TOOL_DELAY_S", defaults.tool_delay_s)),
            max_active_runs=int(env.get("TT_MAX_ACTIVE_RUNS", defaults.max_active_runs)),
            unavailable_tools=frozenset(
                ToolName(name.strip()) for name in unavailable.split(",") if name.strip()
            ),
        )

    def tool_available(self, tool: ToolName) -> bool:
        return tool not in self.unavailable_tools
