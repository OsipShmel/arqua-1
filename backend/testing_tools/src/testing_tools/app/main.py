from fastapi import FastAPI

from testing_tools.api.schemas import HealthResponse, HealthState, ToolName
from testing_tools.app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="testing_tools", version=settings.version)
    app.state.settings = settings

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        tools = {tool: settings.tool_available(tool) for tool in ToolName}
        status = HealthState.OK if all(tools.values()) else HealthState.DEGRADED
        return HealthResponse(status=status, version=settings.version, tools=tools)

    return app
