from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from testing_tools.api.schemas import (
    MicrocksConfig,
    MutationConfig,
    ProblemDetail,
    SchemathesisConfig,
    ToolInfo,
    ToolName,
)
from testing_tools.app.settings import Settings

TOOL_CONFIGS: dict[ToolName, type[BaseModel]] = {
    ToolName.SCHEMATHESIS: SchemathesisConfig,
    ToolName.MICROCKS: MicrocksConfig,
    ToolName.MUTATION: MutationConfig,
}

TOOL_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.SCHEMATHESIS: "Property-based API fuzzing (stub)",
    ToolName.MICROCKS: "Contract testing by OpenAPI examples (stub)",
    ToolName.MUTATION: "Contract-level mutation testing of pytest suites (stub)",
}

PROBLEMS: dict[int | str, dict[str, Any]] = {
    422: {"model": ProblemDetail, "description": "Validation error"},
}


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(responses=PROBLEMS)


@router.get("/tools", response_model=list[ToolInfo], tags=["tools"])
def list_tools(settings: SettingsDep) -> list[ToolInfo]:
    return [
        ToolInfo(
            tool=tool,
            available=settings.tool_available(tool),
            version=settings.version if settings.tool_available(tool) else None,
            description=TOOL_DESCRIPTIONS[tool],
            config_schema=config.model_json_schema(),
        )
        for tool, config in TOOL_CONFIGS.items()
    ]
