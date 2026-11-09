from fastapi import FastAPI
from fastapi.testclient import TestClient

from testing_tools.api.schemas import ErrorCode, RunCreateRequest
from testing_tools.app.errors import ApiError
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings

PROBLEM_JSON = "application/problem+json"


def _app_with_probe_routes() -> FastAPI:
    app = create_app(Settings())

    @app.get("/probe/api-error")
    def api_error() -> None:
        raise ApiError(429, ErrorCode.TOO_MANY_RUNS, "queue is full", headers={"Retry-After": "5"})

    @app.post("/probe/body")
    def body(request: RunCreateRequest) -> None:
        return None

    @app.get("/probe/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    return app


def test_api_error_is_rendered_as_problem_detail() -> None:
    with TestClient(_app_with_probe_routes()) as client:
        r = client.get("/probe/api-error")

    assert r.status_code == 429
    assert r.headers["content-type"] == PROBLEM_JSON
    assert r.headers["retry-after"] == "5"
    assert r.json() == {
        "type": "about:blank",
        "title": "Too many runs",
        "status": 429,
        "detail": "queue is full",
        "code": "too_many_runs",
        "errors": [],
    }


def test_validation_error_lists_field_errors() -> None:
    payload = {
        "contract": {"kind": "inline", "format": "json", "content": "{}"},
        "target": {"base_url": "http://svc:8080"},
        "tools": [{"tool": "schemathesis", "max_examples": 100_000}],
    }
    with TestClient(_app_with_probe_routes()) as client:
        r = client.post("/probe/body", json=payload)

    assert r.status_code == 422
    assert r.headers["content-type"] == PROBLEM_JSON
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["title"] == "Validation error"
    assert body["errors"] == [
        {
            "loc": ["body", "tools", 0, "schemathesis", "max_examples"],
            "msg": "Input should be less than or equal to 10000",
            "type": "less_than_equal",
        }
    ]


def test_unknown_field_is_a_validation_error() -> None:
    payload = {
        "contract": {"kind": "inline", "format": "json", "content": "{}"},
        "target": {"base_url": "http://svc:8080"},
        "tools": [{"tool": "microcks"}],
        "surprise": 1,
    }
    with TestClient(_app_with_probe_routes()) as client:
        r = client.post("/probe/body", json=payload)

    assert r.status_code == 422
    assert r.json()["errors"][0]["type"] == "extra_forbidden"


def test_unhandled_exception_is_internal_error() -> None:
    with TestClient(_app_with_probe_routes(), raise_server_exceptions=False) as client:
        r = client.get("/probe/crash")

    assert r.status_code == 500
    assert r.headers["content-type"] == PROBLEM_JSON
    assert r.json()["code"] == "internal_error"
    assert "boom" not in r.text
