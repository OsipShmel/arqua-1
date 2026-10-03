import math
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from testing_tools.api.schemas import (
    ErrorCode,
    MicrocksConfig,
    MutationConfig,
    ProblemDetail,
    RunCreated,
    RunCreateRequest,
    RunResult,
    RunStatus,
    SchemathesisConfig,
    ToolInfo,
    ToolName,
)
from testing_tools.app.contract import load_contract
from testing_tools.app.engine import StubEngine
from testing_tools.app.errors import ApiError
from testing_tools.app.settings import Settings
from testing_tools.app.store import RunRecord, RunStore
from testing_tools.app.validation import (
    check_path_regexes,
    check_test_suites,
    check_tools_available,
)

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


def _problems(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {status: {"model": ProblemDetail} for status in statuses}


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_store(request: Request) -> RunStore:
    store: RunStore = request.app.state.store
    return store


def get_engine(request: Request) -> StubEngine:
    engine: StubEngine = request.app.state.engine
    return engine


def get_http(request: Request) -> httpx2.AsyncClient:
    client: httpx2.AsyncClient = request.app.state.http
    return client


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[RunStore, Depends(get_store)]
HttpDep = Annotated[httpx2.AsyncClient, Depends(get_http)]
EngineDep = Annotated[StubEngine, Depends(get_engine)]

router = APIRouter(responses=_problems(422))


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


@router.post(
    "/runs",
    status_code=202,
    response_model=RunCreated,
    tags=["runs"],
    responses=_problems(429, 503),
)
async def create_run(
    body: RunCreateRequest,
    response: Response,
    settings: SettingsDep,
    store: StoreDep,
    http: HttpDep,
    engine: EngineDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> RunCreated:
    if idempotency_key is not None and (known := store.by_idempotency_key(idempotency_key)):
        return _created(known, response)
    if store.active_count() >= settings.max_active_runs:
        raise ApiError(
            429,
            ErrorCode.TOO_MANY_RUNS,
            f"{settings.max_active_runs} runs are already active",
            headers={"Retry-After": str(max(1, math.ceil(settings.tool_delay_s)))},
        )
    check_path_regexes(body)
    contract = await load_contract(body.contract, http)
    check_tools_available(body, settings.unavailable_tools)
    check_test_suites(body)

    record = RunRecord.new(body, contract, settings.api_prefix)
    store.add(record, idempotency_key)
    engine.start(record)
    return _created(record, response)


@router.get("/runs/{run_id}", response_model=RunStatus, tags=["runs"], responses=_problems(404))
def get_run(run_id: str, store: StoreDep) -> RunStatus:
    return _record(store, run_id).status_view()


@router.get(
    "/runs/{run_id}/result", response_model=RunResult, tags=["runs"], responses=_problems(404, 409)
)
def get_run_result(run_id: str, store: StoreDep) -> RunResult:
    record = _record(store, run_id)
    if not record.finished:
        raise ApiError(409, ErrorCode.RUN_NOT_FINISHED, f"run is {record.state.value}")
    return record.result_view()


@router.post(
    "/runs/{run_id}/cancel",
    status_code=202,
    response_model=RunStatus,
    tags=["runs"],
    responses=_problems(404, 409),
)
def cancel_run(run_id: str, store: StoreDep, engine: EngineDep) -> RunStatus:
    record = _record(store, run_id)
    if record.finished:
        raise ApiError(409, ErrorCode.RUN_ALREADY_FINISHED, f"run is {record.state.value}")
    engine.cancel(record)
    return record.status_view()


@router.get(
    "/runs/{run_id}/artifacts/{name}",
    response_class=Response,
    tags=["runs"],
    responses={200: {"content": {"application/octet-stream": {}}}, **_problems(404)},
)
def get_artifact(run_id: str, name: str, store: StoreDep) -> Response:
    artifact = _record(store, run_id).artifacts.get(name)
    if artifact is None:
        raise ApiError(404, ErrorCode.ARTIFACT_NOT_FOUND, f"artifact {name} not found")
    return Response(artifact.content, media_type=artifact.media_type)


def _created(record: RunRecord, response: Response) -> RunCreated:
    response.headers["Location"] = record.links.self
    return record.created_view()


def _record(store: RunStore, run_id: str) -> RunRecord:
    record = store.get(run_id)
    if record is None:
        raise ApiError(404, ErrorCode.RUN_NOT_FOUND, f"run {run_id} not found")
    return record
