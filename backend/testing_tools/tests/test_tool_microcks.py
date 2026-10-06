"""MicrocksRunner against a fake Microcks REST API (httpx2.MockTransport)."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx2
import pytest

from testing_tools.api.schemas import (
    ErrorCode,
    MicrocksConfig,
    MicrocksResult,
    Severity,
    ToolState,
)
from testing_tools.app.contract import LoadedContract
from testing_tools.app.settings import Settings
from testing_tools.app.tools.base import ToolError
from testing_tools.app.tools.microcks import MicrocksRunner
from tool_helpers import make_ctx

pytestmark = pytest.mark.anyio

SETTINGS = Settings(
    tool_mode="real",
    microcks_url="http://microcks:8080/",
    microcks_client_id="tt",
    microcks_client_secret="s3cret",
    microcks_poll_interval_s=0.01,
)

FINAL_TEST: dict[str, Any] = {
    "id": "t1",
    "testNumber": 3,
    "inProgress": False,
    "success": False,
    "testCaseResults": [
        {
            "operationName": "GET /pets",
            "success": True,
            "elapsedTime": 12,
            "testStepResults": [{"requestName": "all", "success": True, "elapsedTime": 12}],
        },
        {
            "operationName": "GET /pets/{petId}",
            "success": False,
            "elapsedTime": 30,
            "testStepResults": [
                {"requestName": "rex", "success": False, "elapsedTime": 10,
                 "message": "Response is not valid: id should be integer"},
                {"requestName": "unknown", "success": True, "elapsedTime": 20},
            ],
        },
    ],
}  # fmt: skip

MESSAGES = {
    "GET /pets": [{"request": {"name": "all"}, "response": {"status": "200"}}],
    "GET /pets/{petId}": [
        {
            "request": {"name": "rex", "headers": [{"name": "X-Api-Key", "values": ["k"]}]},
            "response": {
                "status": "200",
                "content": '{"id": "1"}',
                "headers": [{"name": "Content-Type", "values": ["application/json"]}],
            },
        },
        {"request": {"name": "unknown"}, "response": {"status": "404"}},
    ],
}


class FakeMicrocks:
    def __init__(self) -> None:
        self.uploaded: dict[str, Any] = {}
        self.test_request: dict[str, Any] = {}
        self.polls = 0
        self.deleted: list[str] = []
        self.auth: list[str | None] = []
        self.fail_upload = False

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path, method = request.url.path, request.method
        if request.url.host == "kc":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx2.Response(200, json={"access_token": "tok"})
        self.auth.append(request.headers.get("authorization"))
        if path == "/api/keycloak/config":
            return httpx2.Response(
                200, json={"enabled": True, "realm": "microcks", "auth-server-url": "http://kc/auth"}
            )
        if path == "/api/artifact/upload":
            if self.fail_upload:
                return httpx2.Response(400, text="not an artifact")
            body = request.content.decode()
            self.uploaded = json.loads(body[body.index("{") : body.rindex("}") + 1])
            info = self.uploaded["info"]
            return httpx2.Response(201, text=f"{info['title']}:{info['version']}")
        if path.startswith("/api/services/") and method == "GET":
            assert request.url.params["messages"] == "false"
            return httpx2.Response(200, json={"id": "svc-1", "name": unquote(path)})
        if path == "/api/services/svc-1" and method == "DELETE":
            self.deleted.append("svc-1")
            return httpx2.Response(200)
        if path == "/api/tests" and method == "POST":
            self.test_request = json.loads(request.content)
            return httpx2.Response(201, json={"id": "t1", "testNumber": 3, "inProgress": True})
        if path == "/api/tests/t1":
            self.polls += 1
            if self.polls < 2:
                return httpx2.Response(200, json={"id": "t1", "inProgress": True})
            return httpx2.Response(200, json=FINAL_TEST)
        if path.startswith("/api/tests/t1/messages/"):
            case_id = unquote(path.rsplit("/", 1)[1])
            assert case_id.startswith("t1-3-")
            name = case_id.removeprefix("t1-3-").replace("!", "/")
            return httpx2.Response(200, json=MESSAGES[name])
        return httpx2.Response(404)


async def test_full_flow(tmp_path: Path, contract: LoadedContract) -> None:
    fake = FakeMicrocks()
    ctx, collected = make_ctx(
        tmp_path, contract, "http://petstore:8080/", SETTINGS, headers={"X-Api-Key": "k"}
    )
    cfg = MicrocksConfig.model_validate(
        {
            "timeout_ms": 5000,
            "service_name": "Pets",
            "operations_headers": {"globals": {"X-Tenant": "t"}, "GET /pets": {"X-Debug": "1"}},
            "filtered_operations": ["GET /pets", "GET /pets/{petId}"],
        }
    )

    result = await MicrocksRunner(httpx2.MockTransport(fake)).run(cfg, ctx)

    assert fake.uploaded["info"] == {"title": "Pets", "version": "1.0.0"}
    assert fake.test_request == {
        "serviceId": "Pets:1.0.0",
        "testEndpoint": "http://petstore:8080",
        "runnerType": "OPEN_API_SCHEMA",
        "timeout": 5000,
        "filteredOperations": ["GET /pets", "GET /pets/{petId}"],
        "operationsHeaders": {
            "globals": [{"name": "X-Api-Key", "values": "k"}, {"name": "X-Tenant", "values": "t"}],
            "GET /pets": [{"name": "X-Debug", "values": "1"}],
        },
    }
    assert set(fake.auth) == {None, "Bearer tok"}  # keycloak config is fetched anonymously
    assert fake.deleted == ["svc-1"]

    assert isinstance(result, MicrocksResult)
    assert result.state == ToolState.FAILED
    assert (result.test_id, result.service_ref) == ("t1", "Pets:1.0.0")
    assert result.test_url == "http://microcks:8080/#/tests/t1"
    assert result.summary is not None
    assert (result.summary.operations_passed, result.summary.operations_failed) == (1, 1)
    assert (result.summary.steps_total, result.summary.steps_passed) == (3, 2)
    assert result.operations[1].operation.operation_id == "getPet"

    [finding] = result.findings
    assert finding.check == "OPEN_API_SCHEMA" and finding.severity == Severity.HIGH
    assert finding.message == "Response is not valid: id should be integer"
    assert finding.request is not None
    assert finding.request.url == "http://petstore:8080/pets/{petId}"
    assert finding.request.headers == {"X-Api-Key": "***"}
    assert finding.response is not None and finding.response.body == '{"id": "1"}'

    coverage = result.coverage
    assert coverage is not None
    assert coverage.operations_tested == 2
    assert coverage.by_operation[2].observed_status_codes == [200, 404]
    assert "microcks-test-result.json" in collected.artifacts


async def test_rejected_contract_is_a_tool_error(tmp_path: Path, contract: LoadedContract) -> None:
    fake = FakeMicrocks()
    fake.fail_upload = True
    ctx, _ = make_ctx(tmp_path, contract, "http://petstore:8080", SETTINGS)

    with pytest.raises(ToolError) as exc:
        await MicrocksRunner(httpx2.MockTransport(fake)).run(MicrocksConfig(), ctx)

    assert exc.value.code == ErrorCode.TOOL_FAILED
    assert exc.value.details == {"body": "not an artifact"}


async def test_keep_service_and_no_auth(tmp_path: Path, contract: LoadedContract) -> None:
    fake = FakeMicrocks()
    settings = SETTINGS.model_copy(update={"microcks_client_id": ""})
    ctx, _ = make_ctx(tmp_path, contract, "http://petstore:8080", settings)

    await MicrocksRunner(httpx2.MockTransport(fake)).run(MicrocksConfig(cleanup=False), ctx)

    assert fake.deleted == []
    assert set(fake.auth) == {None}
    assert "filteredOperations" not in fake.test_request


async def test_unreachable_microcks(tmp_path: Path, contract: LoadedContract) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    ctx, _ = make_ctx(tmp_path, contract, "http://petstore:8080", SETTINGS)

    with pytest.raises(ToolError) as exc:
        await MicrocksRunner(httpx2.MockTransport(down)).run(MicrocksConfig(), ctx)

    assert exc.value.code == ErrorCode.TOOL_FAILED
    assert "unreachable" in exc.value.message


async def test_not_configured(tmp_path: Path, contract: LoadedContract) -> None:
    ctx, _ = make_ctx(tmp_path, contract, "http://petstore:8080", Settings(tool_mode="real"))

    with pytest.raises(ToolError) as exc:
        await MicrocksRunner().run(MicrocksConfig(), ctx)

    assert exc.value.code == ErrorCode.TOOL_UNAVAILABLE
