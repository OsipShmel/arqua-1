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
