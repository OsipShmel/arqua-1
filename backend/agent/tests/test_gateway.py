import json
from pathlib import Path

import httpx
import pytest

from agent.events import Journal
from agent.gateway import ToolsGateway

RUN_ID = "11111111-1111-4111-8111-111111111111"


def gateway(tmp_path):
    return ToolsGateway(
        {
            "url": "http://tools.test/api/v1",
            "contract": {"openapi": "3.1.0"},
            "target": "http://target.test",
            "directory": str(tmp_path),
        },
        Journal(tmp_path / "events.jsonl", "local-run"),
    )


def install_transport(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_gateway_matches_develop_api_and_saves_artifacts(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert (
                body["contract"]["kind"] == "inline"
                and body["contract"]["format"] == "json"
            )
            assert body["target"]["base_url"] == "http://target.test"
            assert body["labels"] == {"arqa_run_id": "local-run"}
            assert body["tools"] == [{"tool": "schemathesis", "max_examples": 50}]
            return httpx.Response(202, json={"run_id": RUN_ID})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "state": "completed",
                    "metrics": {"contract_coverage": 1},
                    "tools": [
                        {
                            "tool": "schemathesis",
                            "state": "failed",
                            "findings": [{"title": "real finding"}],
                            "artifacts": [
                                {
                                    "name": "junit.xml",
                                    "media_type": "application/xml",
                                    "url": "http://untrusted.invalid/ignored",
                                }
                            ],
                        }
                    ],
                },
            )
        if request.url.path.endswith("/junit.xml"):
            return httpx.Response(200, content=b"<testsuites/>")
        return httpx.Response(200, json={"state": "completed"})

    install_transport(monkeypatch, handler)
    client = gateway(tmp_path)
    compact = json.loads(client.run("schemathesis"))
    assert (
        compact["tools"][0]["state"] == "failed"
    )  # Findings do not become infrastructure errors.
    assert compact["metrics"]["contract_coverage"] == 1
    assert client.outcome()["execution"]["state"] == "failed"
    assert Path(client.artifacts[0]["path"]).read_bytes() == b"<testsuites/>"
    assert all(r.url.host == "tools.test" for r in requests)


def test_gateway_cancels_run_when_polling_fails(tmp_path, monkeypatch):
    paths = []

    def handler(request):
        paths.append((request.method, request.url.path))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"state": "cancelled"})
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": RUN_ID})
        raise httpx.ReadTimeout("poll expired", request=request)

    install_transport(monkeypatch, handler)
    with pytest.raises(httpx.ReadTimeout):
        gateway(tmp_path).run("schemathesis")
    assert ("POST", f"/api/v1/runs/{RUN_ID}/cancel") in paths


def test_gateway_rejects_artifact_traversal(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": RUN_ID})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "state": "completed",
                    "tools": [{"artifacts": [{"name": "../outside.txt"}]}],
                },
            )
        return httpx.Response(200, json={"state": "completed"})

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="путь"):
        gateway(tmp_path).run("schemathesis")
    assert not (tmp_path / "outside.txt").exists()


def test_gateway_rejects_unselected_tool_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(
        httpx, "Client", lambda **_: pytest.fail("network must not start")
    )
    with pytest.raises(ValueError, match="не разрешён"):
        gateway(tmp_path).run("microcks")
