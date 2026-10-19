import importlib.util
from pathlib import Path
from enum import StrEnum
from typing import Annotated, Any, ClassVar

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from testing_tools.api.schemas import ToolName

VERSION = "0.0.1"


class ToolMode(StrEnum):
    REAL = "real"
    """Run Schemathesis, Microcks and the mutation runner for real."""
    STUB = "stub"
    """Fake every tool with a delay and a result derived from the contract."""


class Settings(BaseSettings):
    """Read from TT_* process env, then `.env` in the working directory; see `.env.example`."""

    model_config = SettingsConfigDict(env_prefix="TT_", env_file=".env", frozen=True)

    version: ClassVar[str] = VERSION

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    tool_mode: ToolMode = ToolMode.REAL
    tool_delay_s: float = Field(default=1.0, ge=0)
    """Simulated duration of each stub tool (`tool_mode=stub` only)."""
    max_active_runs: int = Field(default=16, ge=1)
    unavailable_tools: Annotated[frozenset[ToolName], NoDecode] = frozenset()
    """Comma-separated in env: `microcks,mutation`."""
    work_dir: str = ""
    """Where per-tool scratch directories go; empty = system temp dir."""
    keep_work_dirs: bool = False
    """Leave scratch directories behind for debugging."""
    microcks_url: str = ""
    """Microcks base URL, e.g. `http://microcks:8080`; empty = Microcks unavailable."""
    microcks_client_id: str = ""
    """Keycloak service account for Microcks; empty = no auth (dev / uber image)."""
    microcks_client_secret: str = ""
    microcks_poll_interval_s: float = Field(default=1.0, gt=0)
    results_dir: str = "results"
    """Finished runs are saved here (JSON + Markdown); empty = don't save."""
    max_runs_in_memory: int = Field(default=500, ge=1)
    """Finished runs kept in memory; older ones are served from `results_dir`."""
    isolate_test_network: bool = True
    """Agent tests may only connect to the service under test (through the proxy)."""
    evomaster_jar: str = ""
    """Path to evomaster.jar; empty = EvoMaster unavailable."""
    java_bin: str = "java"
    """Java 17+ used to run EvoMaster."""
    uv_bin: str = "uv"
    """Used to build a venv for mutation test suites that declare `requirements`."""

    @field_validator("unavailable_tools", mode="before")
    @classmethod
    def _split_tools(cls, value: Any) -> Any:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value

    def tool_available(self, tool: ToolName) -> bool:
        if tool in self.unavailable_tools:
            return False
        if self.tool_mode == ToolMode.STUB:
            return True
        match tool:
            case ToolName.SCHEMATHESIS:
                return importlib.util.find_spec("schemathesis") is not None
            case ToolName.MICROCKS:
                return bool(self.microcks_url)
            case ToolName.MUTATION:
                return True
            case ToolName.EVOMASTER:
                return bool(self.evomaster_jar) and Path(self.evomaster_jar).is_file()

    def down_tools(self) -> frozenset[ToolName]:
        return frozenset(tool for tool in ToolName if not self.tool_available(tool))
