"""Microcks contract test over its REST API: upload the contract, launch a test, poll, collect."""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx2

from testing_tools.api.schemas import (
    ErrorCode,
    Finding,
    MicrocksConfig,
    MicrocksOperationResult,
    MicrocksResult,
    MicrocksStepResult,
    MicrocksSummary,
    Severity,
    ToolConfig,
    ToolName,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import ContractOperation
from testing_tools.app.tools.base import ToolContext, ToolError
from testing_tools.app.tools.common import (
    Masker,
    Observations,
    OperationIndex,
    build_coverage,
    curl,
    request_snapshot,
    response_snapshot,
)

HTTP_TIMEOUT_S = 30.0
POLL_GRACE_S = 60.0
"""Extra wait over the test's own timeout before we give up polling."""

log = logging.getLogger(__name__)


class MicrocksRunner:
    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        """`transport` replaces the network to Microcks (tests)."""
        self._transport = transport

    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        assert isinstance(config, MicrocksConfig)
        settings = ctx.settings
        if not settings.microcks_url:
            raise ToolError(ErrorCode.TOOL_UNAVAILABLE, "TT_MICROCKS_URL is not set")
        base = settings.microcks_url.rstrip("/")
        async with httpx2.AsyncClient(
            base_url=f"{base}/api", timeout=HTTP_TIMEOUT_S, transport=self._transport
        ) as http:
            api = _Api(http)
            try:
                await api.authenticate(settings.microcks_client_id, settings.microcks_client_secret)
                return await self._run(config, ctx, api, base)
            except httpx2.HTTPError as exc:
                raise ToolError(ErrorCode.TOOL_FAILED, f"Microcks is unreachable: {exc}") from exc

    async def _run(
        self, config: MicrocksConfig, ctx: ToolContext, api: _Api, ui_base: str
    ) -> MicrocksResult:
        document = dict(ctx.contract.document)
        info = dict(document.get("info") or {})
        info["title"] = config.service_name or info.get("title")
        info["version"] = config.service_version or info.get("version")
        document["info"] = info

        service_ref = await api.upload(json.dumps(document).encode())
        ctx.set_progress(0.1)
        service_id: str | None = None
        try:
            service_id = await api.service_id(service_ref)
            test = await api.launch_test(
                {
                    "serviceId": service_ref,
                    "testEndpoint": ctx.base_url,
                    "runnerType": config.runner.value,
                    "timeout": config.timeout_ms,
                    "filteredOperations": config.filtered_operations,
                    "operationsHeaders": _operations_headers(config, ctx),
                    "secretName": config.secret_name,
                }
            )
            test = await self._wait(api, ctx, test, config.timeout_ms)
            ctx.add_artifact(
                "microcks-test-result.json", "application/json", json.dumps(test, indent=2).encode()
            )
            messages = await api.messages(test)
            ctx.set_progress(0.95)
            return _result(config, ctx, test, messages, service_ref, ui_base)
        finally:
            if config.cleanup and service_id is not None:
                await asyncio.shield(api.delete_service(service_id))

    async def _wait(
        self, api: _Api, ctx: ToolContext, test: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_ms / 1000 + POLL_GRACE_S
        started = time.monotonic()
        while test.get("inProgress", True):
            if time.monotonic() > deadline:
                raise ToolError(
                    ErrorCode.TIMEOUT, f"Microcks test {test.get('id')} is still in progress"
                )
            await asyncio.sleep(ctx.settings.microcks_poll_interval_s)
            test = await api.test(str(test["id"]))
            elapsed = time.monotonic() - started
            ctx.set_progress(min(0.9, 0.1 + 0.8 * elapsed * 1000 / timeout_ms))
        return test


class _Api:
    def __init__(self, http: httpx2.AsyncClient) -> None:
        self._http = http

    async def authenticate(self, client_id: str, client_secret: str) -> None:
        if not client_id:
            return
        config = _json(await self._http.get("/keycloak/config"), "keycloak config")
        if not config.get("enabled"):
            return
        token_url = (
            f"{str(config['auth-server-url']).rstrip('/')}"
            f"/realms/{config['realm']}/protocol/openid-connect/token"
        )
        response = await self._http.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        token = _json(response, "Keycloak token").get("access_token")
        if not token:
            raise ToolError(ErrorCode.TOOL_FAILED, "Keycloak returned no access_token")
        self._http.headers["Authorization"] = f"Bearer {token}"

    async def upload(self, content: bytes) -> str:
        response = await self._http.post(
            "/artifact/upload",
            params={"mainArtifact": "true"},
            files={"file": ("openapi.json", content, "application/json")},
        )
        if response.status_code != 201:
            raise ToolError(
                ErrorCode.TOOL_FAILED,
                f"Microcks rejected the contract: HTTP {response.status_code}",
                {"body": response.text[:2000]},
            )
        return response.text.strip()  # "<name>:<version>"

    async def service_id(self, service_ref: str) -> str:
        response = await self._http.get(
            f"/services/{quote(service_ref, safe=':')}", params={"messages": "false"}
        )
        return str(_json(response, "service lookup")["id"])

    async def launch_test(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = {k: v for k, v in body.items() if v not in (None, {}, [])}
        return _json(await self._http.post("/tests", json=payload), "test launch")

    async def test(self, test_id: str) -> dict[str, Any]:
        return _json(await self._http.get(f"/tests/{test_id}"), "test result")

    async def messages(self, test: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Request/response pairs per operation name; best effort."""
        out: dict[str, list[dict[str, Any]]] = {}
        number = test.get("testNumber")
        number_str = str(int(number)) if isinstance(number, int | float) else str(number)
        for case in test.get("testCaseResults") or []:
            name = str(case.get("operationName", ""))
            case_id = f"{test['id']}-{number_str}-{name}".replace("/", "!")
            try:
                response = await self._http.get(
                    f"/tests/{test['id']}/messages/{quote(case_id, safe='')}"
                )
                pairs = response.json() if response.is_success else []
            except (httpx2.HTTPError, ValueError) as exc:
                log.warning("Microcks messages for %s unavailable: %s", name, exc)
                pairs = []
            out[name] = pairs if isinstance(pairs, list) else []
        return out

    async def delete_service(self, service_id: str) -> None:
        try:
            await self._http.delete(f"/services/{service_id}")
        except httpx2.HTTPError as exc:
            log.warning("Microcks service %s was not deleted: %s", service_id, exc)


def _json(response: httpx2.Response, what: str) -> dict[str, Any]:
    if not response.is_success:
        raise ToolError(
            ErrorCode.TOOL_FAILED,
            f"Microcks {what} failed: HTTP {response.status_code}",
            {"body": response.text[:2000]},
        )
    data = response.json()
    if not isinstance(data, dict):
        raise ToolError(ErrorCode.TOOL_FAILED, f"Microcks {what}: unexpected payload")
    return data


def _operations_headers(
    config: MicrocksConfig, ctx: ToolContext
) -> dict[str, list[dict[str, str]]]:
    merged: dict[str, dict[str, str]] = {
        key: dict(value) for key, value in config.operations_headers.items()
    }
    merged["globals"] = {**ctx.target.headers, **merged.get("globals", {})}
    return {
        operation: [{"name": name, "values": value} for name, value in headers.items()]
        for operation, headers in merged.items()
        if headers
    }


def _result(
    config: MicrocksConfig,
    ctx: ToolContext,
    test: dict[str, Any],
    messages: dict[str, list[dict[str, Any]]],
    service_ref: str,
    ui_base: str,
) -> MicrocksResult:
    index = OperationIndex(ctx.contract.operations)
    masker = Masker([*ctx.target.headers, *_header_names(config)])
    observed = Observations()
    operations: list[MicrocksOperationResult] = []
    findings: list[Finding] = []

    for case in test.get("testCaseResults") or []:
        name = str(case.get("operationName", ""))
        op = index.by_label(name)
        if op is None:
            log.warning("Microcks operation %r is not in the contract", name)
            continue
        pairs = {
            str((pair.get("request") or {}).get("name")): pair for pair in messages.get(name, [])
        }
        steps: list[MicrocksStepResult] = []
        for step in case.get("testStepResults") or []:
            request_name = str(step.get("requestName") or "")
            pair = pairs.get(request_name)
            status = _status(pair)
            observed.add(op, status)
            result = MicrocksStepResult(
                request_name=request_name,
                success=bool(step.get("success")),
                elapsed_ms=step.get("elapsedTime"),
                message=step.get("message") or None,
            )
            steps.append(result)
            if not result.success:
                findings.append(_finding(config, ctx, op, result, pair, masker))
        operations.append(
            MicrocksOperationResult(
                operation=op.ref,
                microcks_operation=name,
                success=bool(case.get("success")),
                elapsed_ms=case.get("elapsedTime"),
                steps=steps,
            )
        )

    passed = sum(o.success for o in operations)
    all_steps = [s for o in operations for s in o.steps]
    return MicrocksResult(
        state=ToolState.PASSED if test.get("success") and not findings else ToolState.FAILED,
        test_id=str(test.get("id")),
        test_url=f"{ui_base}/#/tests/{test.get('id')}",
        service_ref=service_ref,
        summary=MicrocksSummary(
            operations_total=len(operations),
            operations_passed=passed,
            operations_failed=len(operations) - passed,
            steps_total=len(all_steps),
            steps_passed=sum(s.success for s in all_steps),
        ),
        operations=operations,
        coverage=build_coverage(ctx.contract.operations, observed),
        findings=findings,
    )


def _finding(
    config: MicrocksConfig,
    ctx: ToolContext,
    op: ContractOperation,
    step: MicrocksStepResult,
    pair: dict[str, Any] | None,
    masker: Masker,
) -> Finding:
    request = response = None
    if pair:
        req = pair.get("request") or {}
        request = request_snapshot(
            op.ref.method.value,
            ctx.base_url + op.ref.path,
            _headers(req.get("headers")),
            req.get("content"),
            masker,
        )
        resp = pair.get("response") or {}
        status = _status(pair)
        if status is not None:
            response = response_snapshot(
                status, _headers(resp.get("headers")), resp.get("content"), step.elapsed_ms
            )
    key = "\x00".join((op.ref.method.value, op.ref.path, step.request_name, step.message or ""))
    return Finding(
        id="mk-" + hashlib.sha1(key.encode()).hexdigest()[:10],
        tool=ToolName.MICROCKS,
        check=config.runner.value,
        severity=Severity.HIGH,
        operation=op.ref,
        title=f"Example '{step.request_name}' failed",
        message=step.message or "Microcks reported a failed test step",
        request=request,
        response=response,
        reproduce=curl(request) if request else None,
    )


def _status(pair: dict[str, Any] | None) -> int | None:
    status = ((pair or {}).get("response") or {}).get("status")
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, list):
        return {}
    out: dict[str, str] = {}
    for header in headers:
        if isinstance(header, dict) and "name" in header:
            values = header.get("values")
            out[str(header["name"])] = (
                ", ".join(map(str, values)) if isinstance(values, list) else str(values or "")
            )
    return out


def _header_names(config: MicrocksConfig) -> list[str]:
    return [name for headers in config.operations_headers.values() for name in headers]
