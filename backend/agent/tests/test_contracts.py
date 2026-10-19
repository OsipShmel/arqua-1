import json

import httpx
import pytest

from agent.contracts import read_contract

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Pets", "version": "1"},
    "paths": {"/pets": {"get": {"responses": {"200": {"description": "OK"}}}}},
}


def test_yaml_with_unquoted_response_code(tmp_path):
    source = tmp_path / "pets.yaml"
    source.write_text(
        "openapi: 3.0.3\ninfo: {title: Pets, version: '1'}\n"
        "paths:\n  /pets:\n    get:\n      responses:\n"
        "        200: {description: OK}\n",
        encoding="utf-8",
    )
    context = read_contract(str(source))
    assert "200" in context.contract.operations[0].responses


def test_path_item_ref_is_counted_and_summarized(tmp_path):
    document = {
        **SPEC,
        "paths": {"/pets": {"$ref": "#/x-path"}},
        "x-path": SPEC["paths"]["/pets"],
    }
    source = tmp_path / "path-ref.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    context = read_contract(str(source))
    assert context.metadata()["operations_total"] == 1
    assert context.summary()["operations"][0]["path"] == "/pets"
    assert "x-path" not in context.summary()


def test_yaml_api_dates_and_enum_words_keep_their_json_types(tmp_path):
    source = tmp_path / "dates.yaml"
    source.write_text(
        "openapi: 3.1.0\ninfo: {title: Example, version: '1'}\npaths:\n  /x:\n    get:\n      responses:\n        200:\n          description: OK\n          content:\n            application/json:\n              schema:\n                type: string\n                example: 2026-10-19\n                enum: [on, off]\n",
        encoding="utf-8",
    )
    context = read_contract(str(source))
    schema = context.summary()["operations"][0]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["example"] == "2026-10-19" and schema["enum"] == ["on", "off"]
    assert context.metadata()["input_format"] == "yaml"


def test_url_download_uses_content_not_suffix(monkeypatch):
    request = httpx.Request("GET", "http://contract.test/openapi")

    def get(url, **kwargs):
        assert url == str(request.url) and kwargs["timeout"] == 4
        return httpx.Response(200, request=request, content=json.dumps(SPEC).encode())

    monkeypatch.setattr(httpx, "get", get)
    assert read_contract(str(request.url), timeout=4).contract.title == "Pets"


def test_url_http_error_is_propagated(monkeypatch):
    request = httpx.Request("GET", "http://contract.test/openapi")
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: httpx.Response(404, request=request)
    )
    with pytest.raises(httpx.HTTPStatusError):
        read_contract(str(request.url))


@pytest.mark.parametrize(
    "change", [{"openapi": "2.0"}, {"paths": []}, {"paths": {}}, {"info": []}]
)
def test_invalid_contract_is_rejected(tmp_path, change):
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({**SPEC, **change}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_contract(str(source))


def test_agent_context_preserves_constraints_security_and_reference_targets(tmp_path):
    document = {
        **SPEC,
        "security": [{"bearerAuth": []}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "Count": {"type": "integer", "multipleOf": 5, "const": 10},
                "Nullable": {"type": ["string", "null"], "default": None},
                "Free": True,
            },
            "responses": {
                "Ok": {
                    "description": "Referenced response",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Count"}
                        }
                    },
                }
            },
        },
        "paths": {
            "/pets": {
                "get": {
                    "security": [],
                    "responses": {"200": {"$ref": "#/components/responses/Ok"}},
                }
            }
        },
        "x-team-metadata": {"owner": "ARQA"},
    }
    source = tmp_path / "complete.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    context = read_contract(str(source))
    task = context.task("http://target.test")
    provided = task.split("<contract_json>\n", 1)[1].split("\n</contract_json>", 1)[0]
    summary = json.loads(provided)
    assert summary != document and "paths" not in summary
    assert (
        summary["operations"][0]["responses"]["200"]["description"]
        == "Referenced response"
    )
    assert summary["schemas"]["#/components/schemas/Count"] == {
        "type": "integer",
        "multipleOf": 5,
        "const": 10,
    }
    assert "#/components/schemas/Nullable" not in summary["schemas"]
    assert "x-team-metadata" not in provided
    assert '"raw"' not in provided
