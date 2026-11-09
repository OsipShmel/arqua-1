import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_runs_create import RUNS, run_request
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings


@pytest.fixture
def slow_client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(tool_delay_s=60))) as c:
        yield c


def wait_for(client: TestClient, run_id: str, *states: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status: dict[str, Any] = client.get(f"{RUNS}/{run_id}").json()
        if status["state"] in states:
            return status
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {states}")


def test_completed_run_result(client: TestClient) -> None:
    run_id = client.post(RUNS, json=run_request()).json()["run_id"]
    wait_for(client, run_id, "completed")

    r = client.get(f"{RUNS}/{run_id}/result")

    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "completed"
    assert body["labels"] == {"agent_session": "s-81"}
    assert body["contract"]["title"] == "Petstore API"
    assert body["contract"]["operations_total"] == 4
    assert body["target_base_url"].startswith("http://petstore:8080")
    assert body["duration_s"] >= 0
    assert body["error"] is None
    assert [(t["tool"], t["state"]) for t in body["tools"]] == [
        ("schemathesis", "passed"),
        ("microcks", "passed"),
    ]


def test_result_of_unfinished_run_is_409(slow_client: TestClient) -> None:
    run_id = slow_client.post(RUNS, json=run_request()).json()["run_id"]

    r = slow_client.get(f"{RUNS}/{run_id}/result")

    assert r.status_code == 409
    assert r.json()["code"] == "run_not_finished"


def test_cancel_running_run(slow_client: TestClient) -> None:
    run_id = slow_client.post(RUNS, json=run_request()).json()["run_id"]
    wait_for(slow_client, run_id, "running")

    r = slow_client.post(f"{RUNS}/{run_id}/cancel")

    assert r.status_code == 202
    assert r.json()["run_id"] == run_id
    status = wait_for(slow_client, run_id, "cancelled")
    assert [t["state"] for t in status["tools"]] == ["cancelled", "skipped"]

    result = slow_client.get(f"{RUNS}/{run_id}/result").json()
    assert [(t["tool"], t["state"]) for t in result["tools"]] == [
        ("schemathesis", "cancelled"),
        ("microcks", "skipped"),
    ]
    assert result["tools"][0]["summary"] is None


def test_cancel_finished_run_is_409(client: TestClient) -> None:
    run_id = client.post(RUNS, json=run_request()).json()["run_id"]
    wait_for(client, run_id, "completed")

    r = client.post(f"{RUNS}/{run_id}/cancel")

    assert r.status_code == 409
    assert r.json()["code"] == "run_already_finished"


def test_artifact_download(client: TestClient) -> None:
    run_id = client.post(RUNS, json=run_request()).json()["run_id"]
    wait_for(client, run_id, "completed")
    result = client.get(f"{RUNS}/{run_id}/result").json()
    ref = result["tools"][0]["artifacts"][0]

    r = client.get(ref["url"])

    assert r.status_code == 200
    assert r.headers["content-type"] == ref["media_type"]
    assert len(r.content) == ref["size_bytes"]
    assert r.json()["tool"] == "schemathesis"


def test_unknown_artifact_is_404(client: TestClient) -> None:
    run_id = client.post(RUNS, json=run_request()).json()["run_id"]

    r = client.get(f"{RUNS}/{run_id}/artifacts/nope.json")

    assert r.status_code == 404
    assert r.json()["code"] == "artifact_not_found"


@pytest.mark.parametrize(
    ("method", "suffix"), [("GET", "/result"), ("POST", "/cancel"), ("GET", "/artifacts/x")]
)
def test_unknown_run_is_404_everywhere(client: TestClient, method: str, suffix: str) -> None:
    r = client.request(method, f"{RUNS}/7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31{suffix}")

    assert r.status_code == 404
    assert r.json()["code"] == "run_not_found"
