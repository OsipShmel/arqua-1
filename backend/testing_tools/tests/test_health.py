from fastapi.testclient import TestClient

from testing_tools.api.schemas import ToolName
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings


def test_health_ok_when_all_tools_available(client: TestClient) -> None:
    r = client.get("/health")

    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "version": "0.0.1",
        "tools": {"schemathesis": True, "microcks": True, "mutation": True},
    }


def test_health_degraded_when_a_tool_is_unavailable() -> None:
    settings = Settings(unavailable_tools=frozenset({ToolName.MICROCKS}))
    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["tools"]["microcks"] is False
    assert body["tools"]["schemathesis"] is True
