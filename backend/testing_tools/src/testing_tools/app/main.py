import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from testing_tools.api.schemas import HealthResponse, HealthState, ToolName
from testing_tools.app.engine import StubEngine
from testing_tools.app.errors import install_error_handlers
from testing_tools.app.routes import router
from testing_tools.app.settings import Settings
from testing_tools.app.store import RunStore


def create_app(
    settings: Settings | None = None, transport: httpx2.AsyncBaseTransport | None = None
) -> FastAPI:
    """`transport` replaces the network for contract downloads (tests)."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(transport=transport) as http:
            app.state.http = http
            yield
        tasks = [r.task for r in app.state.store.all() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="testing_tools", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.store = RunStore()
    app.state.engine = StubEngine(settings)
    install_error_handlers(app)
    app.include_router(router, prefix=settings.api_prefix)

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        tools = {tool: settings.tool_available(tool) for tool in ToolName}
        status = HealthState.OK if all(tools.values()) else HealthState.DEGRADED
        return HealthResponse(status=status, version=settings.version, tools=tools)

    return app
