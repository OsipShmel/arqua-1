from typing import Any
from uuid import UUID

import httpx2
from fastapi.testclient import TestClient

from petstore import PETSTORE_JSON, inline_contract
from testing_tools.api.schemas import ToolName
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings

RUNS = "/api/v0/runs"


def run_request(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract": inline_contract(),
        "target": {"base_url": "http://petstore:8080"},
        "tools": [{"tool": "schemathesis"}, {"tool": "microcks"}],
        "labels": {"agent_session": "s-81"},
    }
    body.update(overrides)
    return body


def mutation_tool(files: dict[str, str]) -> dict[str, Any]:
    return {"tool": "mutation", "tests": {"kind": "inline", "files": files}}


def test_create_run_returns_202_with_links(client: TestClient) -> None:
    r = client.post(RUNS, json=run_request())

    assert r.status_code == 202
    body = r.json()
    run_id = UUID(body["run_id"])
    assert body["state"] == "queued"
    assert body["created_at"].endswith("Z") or body["created_at"].endswith("+00:00")
    assert body["links"] == {
        "self": f"{RUNS}/{run_id}",
        "result": f"{RUNS}/{run_id}/result",
        "cancel": f"{RUNS}/{run_id}/cancel",
    }
    assert r.headers["location"] == f"{RUNS}/{run_id}"


def test_status_echoes_tools_and_labels(client: TestClient) -> None:
    run_id = client.post(RUNS, json=run_request()).json()["run_id"]

    r = client.get(f"{RUNS}/{run_id}")

    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == run_id
    assert [t["tool"] for t in body["tools"]] == ["schemathesis", "microcks"]
    assert body["labels"] == {"agent_session": "s-81"}
    assert body["links"]["self"] == f"{RUNS}/{run_id}"


def test_unknown_run_is_404(client: TestClient) -> None:
    for run_id in ("7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31", "not-a-uuid"):
        r = client.get(f"{RUNS}/{run_id}")

        assert r.status_code == 404
        assert r.json()["code"] == "run_not_found"


def test_invalid_contract_is_rejected(client: TestClient) -> None:
    contract = {"kind": "inline", "format": "yaml", "content": "swagger: '2.0'"}

    r = client.post(RUNS, json=run_request(contract=contract))

    assert r.status_code == 422
    assert r.json()["code"] == "contract_invalid"


def test_unreachable_contract_url_is_rejected() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/openapi.json":
            return httpx2.Response(200, text=PETSTORE_JSON)
        return httpx2.Response(503)

    app = create_app(Settings(tool_delay_s=0), transport=httpx2.MockTransport(handler))
    with TestClient(app) as client:
        ok = client.post(
            RUNS, json=run_request(contract={"kind": "url", "url": "http://svc/openapi.json"})
        )
        down = client.post(
            RUNS, json=run_request(contract={"kind": "url", "url": "http://svc/missing.json"})
        )

    assert ok.status_code == 202
    assert down.status_code == 422
    assert down.json()["code"] == "contract_unreachable"


def test_unavailable_tool_is_503() -> None:
    settings = Settings(tool_delay_s=0, unavailable_tools=frozenset({ToolName.MICROCKS}))
    with TestClient(create_app(settings)) as client:
        r = client.post(RUNS, json=run_request())

    assert r.status_code == 503
    assert r.json()["code"] == "tool_unavailable"
    assert "microcks" in r.json()["detail"]


def test_mutation_suite_paths_must_be_relative(client: TestClient) -> None:
    for path in ("../escape.py", "tests/../../escape.py", "/etc/test_x.py", ""):
        r = client.post(RUNS, json=run_request(tools=[mutation_tool({path: "x = 1"})]))

        assert r.status_code == 422, path
        assert r.json()["code"] == "test_suite_invalid"


def test_duplicate_tools_are_a_validation_error(client: TestClient) -> None:
    r = client.post(RUNS, json=run_request(tools=[{"tool": "microcks"}, {"tool": "microcks"}]))

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_invalid_path_regex_is_a_validation_error(client: TestClient) -> None:
    tool = {"tool": "schemathesis", "operations": {"include": {"path_regex": ["("]}}}

    r = client.post(RUNS, json=run_request(tools=[tool]))

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["loc"] == [
        "body", "tools", 0, "operations", "include", "path_regex", 0
    ]


def test_idempotency_key_returns_the_same_run(client: TestClient) -> None:
    first = client.post(RUNS, json=run_request(), headers={"Idempotency-Key": "k-1"})
    again = client.post(RUNS, json=run_request(), headers={"Idempotency-Key": "k-1"})
    other = client.post(RUNS, json=run_request(), headers={"Idempotency-Key": "k-2"})

    assert again.status_code == 202
    assert again.json() == first.json()
    assert other.json()["run_id"] != first.json()["run_id"]


def test_too_many_active_runs_is_429() -> None:
    settings = Settings(tool_delay_s=60, max_active_runs=1)
    with TestClient(create_app(settings)) as client:
        first = client.post(RUNS, json=run_request())
        second = client.post(RUNS, json=run_request())

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "too_many_runs"
    assert int(second.headers["retry-after"]) > 0
