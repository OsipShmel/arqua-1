import json
from pathlib import Path

import httpx
import pytest

from agent.events import Journal
from agent.gateway import ToolsGateway

RUN_ID = "11111111-1111-4111-8111-111111111111"


def gateway(tmp_path, **options):
    return ToolsGateway(
        {
            "url": "http://tools.test/api/v1",
            "contract": {"openapi": "3.1.0"},
            "target": "http://target.test",
            "directory": str(tmp_path),
            **options,
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


def test_gateway_skips_oversized_artifacts_but_keeps_the_run(tmp_path, monkeypatch):
    requested = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": RUN_ID})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "state": "completed",
                    "tools": [
                        {
                            "tool": "schemathesis",
                            "state": "failed",
                            "artifacts": [
                                # Real Schemathesis HAR/NDJSON reach tens of MB.
                                {"name": "schemathesis.har", "size_bytes": 30_000_000},
                                {"name": "events.ndjson"},
                                {"name": "junit.xml", "size_bytes": 13},
                            ],
                        }
                    ],
                },
            )
        if "/artifacts/" in request.url.path:
            requested.append(request.url.path.rsplit("/", 1)[-1])
            if request.url.path.endswith("/events.ndjson"):
                return httpx.Response(200, content=b"x" * 200)
            return httpx.Response(200, content=b"<testsuites/>")
        return httpx.Response(200, json={"state": "completed"})

    install_transport(monkeypatch, handler)
    monkeypatch.setattr("agent.gateway.ARTIFACT_BUDGET", 100)
    client = gateway(tmp_path)
    compact = json.loads(client.run("schemathesis"))
    # The declared size is checked before downloading; the undeclared one by bytes.
    assert requested == ["junit.xml", "events.ndjson"]
    assert compact["artifacts_skipped"] == ["events.ndjson", "schemathesis.har"]
    assert [Path(a["path"]).name for a in client.artifacts] == ["junit.xml"]
    skipped = client.outcome()["execution"]["skipped_artifacts"]
    assert {s["name"] for s in skipped} == {"schemathesis.har", "events.ndjson"}
    assert not list(tmp_path.rglob("events.ndjson"))


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


def test_gateway_sends_evomaster_time_budget(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["tools"] == [{"tool": "evomaster", "max_time_s": 30}]
            assert body["timeout_s"] == 190
            return httpx.Response(202, json={"run_id": RUN_ID})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "state": "completed",
                    "tools": [
                        {
                            "tool": "evomaster",
                            "state": "failed",
                            "summary": {"tests_generated": 3, "faults": 2},
                            "findings": [{"title": "HTTP Status 500"}],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"state": "completed"})

    install_transport(monkeypatch, handler)
    client = gateway(tmp_path, tools=["evomaster"], timeout=190)
    compact = json.loads(client.run("evomaster", max_time_s=30))
    assert compact["tools"][0]["summary"]["faults"] == 2
    assert client.outcome()["execution"]["state"] == "failed"


@pytest.mark.parametrize(("max_time_s", "match"), [(5, "от 10"), (31, "tools-timeout")])
def test_gateway_rejects_evomaster_budget_before_network(
    tmp_path, monkeypatch, max_time_s, match
):
    monkeypatch.setattr(
        httpx, "Client", lambda **_: pytest.fail("network must not start")
    )
    with pytest.raises(ValueError, match=match):
        gateway(tmp_path, tools=["evomaster"], timeout=190).run(
            "evomaster", max_time_s=max_time_s
        )


def test_tool_error_is_returned_to_the_model(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(
            422, json={"code": "validation_error", "message": "unknown tool evomaster"}
        )

    install_transport(monkeypatch, handler)
    client = gateway(tmp_path, tools=["schemathesis", "evomaster"], timeout=300)
    [run_api_checks] = client.as_tools()

    # Disallowed tool: refused before the network, the run goes on.
    refused = run_api_checks.invoke({"tool_name": "microcks"})
    # The service rejects the request: its explanation reaches the model.
    rejected = run_api_checks.invoke({"tool_name": "evomaster", "max_time_s": 10})

    assert "не разрешён" in refused
    assert "422" in rejected and "unknown tool evomaster" in rejected
    execution = client.outcome()["execution"]
    assert execution["state"] == "error"
    assert [e["tool"] for e in execution["tool_errors"]] == ["microcks", "evomaster"]
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"gateway.error"') == 2


def test_run_level_cause_is_in_execution(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": RUN_ID})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "state": "failed",
                    "error": {
                        "code": "target_unreachable",
                        "message": "http://127.0.0.1:9 refused connection",
                    },
                    "tools": [{"tool": "schemathesis", "state": "skipped"}],
                },
            )
        return httpx.Response(200, json={"state": "failed"})

    install_transport(monkeypatch, handler)
    client = gateway(tmp_path)
    client.run("schemathesis")

    [run] = client.outcome()["execution"]["runs"]
    assert run["error"]["code"] == "target_unreachable"
    assert "недоступен" in run["cause"]
