from typing import Annotated, Any, ClassVar

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from testing_tools.api.schemas import ToolName

VERSION = "0.0.1"


class Settings(BaseSettings):
    """Read from TT_* process env, then `.env` in the working directory; see `.env.example`."""

    model_config = SettingsConfigDict(env_prefix="TT_", env_file=".env", frozen=True)

    version: ClassVar[str] = VERSION

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v0"
    tool_delay_s: float = Field(default=1.0, ge=0)
    """Simulated duration of each stub tool."""
    max_active_runs: int = Field(default=16, ge=1)
    unavailable_tools: Annotated[frozenset[ToolName], NoDecode] = frozenset()
    """Comma-separated in env: `microcks,mutation`."""

    @field_validator("unavailable_tools", mode="before")
    @classmethod
    def _split_tools(cls, value: Any) -> Any:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value

    def tool_available(self, tool: ToolName) -> bool:
        return tool not in self.unavailable_tools
