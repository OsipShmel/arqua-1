from fastapi.testclient import TestClient

from testing_tools.api.schemas import MicrocksConfig, MutationConfig, SchemathesisConfig, ToolName
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings


def test_tools_lists_every_tool_with_its_config_schema(client: TestClient) -> None:
    r = client.get("/api/v0/tools")

    assert r.status_code == 200
    tools = r.json()
    assert [t["tool"] for t in tools] == ["schemathesis", "microcks", "mutation"]
    assert all(t["available"] for t in tools)
    assert all(t["description"] for t in tools)
    assert tools[0]["config_schema"] == SchemathesisConfig.model_json_schema()
    assert tools[1]["config_schema"] == MicrocksConfig.model_json_schema()
    assert tools[2]["config_schema"] == MutationConfig.model_json_schema()


def test_unavailable_tool_has_no_version() -> None:
    settings = Settings(unavailable_tools=frozenset({ToolName.MUTATION}))
    with TestClient(create_app(settings)) as client:
        mutation = client.get("/api/v0/tools").json()[2]

    assert mutation["available"] is False
    assert mutation["version"] is None


def test_tools_follow_configured_prefix() -> None:
    with TestClient(create_app(Settings(api_prefix="/api/v1"))) as client:
        assert client.get("/api/v1/tools").status_code == 200
        assert client.get("/api/v0/tools").status_code == 404
