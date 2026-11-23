import json

import pytest

from agent.events import Journal
from agent.pytest_generation import PytestGeneration

CONTRACT = {
    "paths": {
        "/items": {
            "post": {
                "requestBody": {},
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    }
                },
            }
        },
        "/items/{itemId}": {"get": {"responses": {"200": {"description": "OK"}}}},
    },
    "components": {"schemas": {"Item": {"type": "object", "required": ["id"]}}},
}


def test_saves_validated_scenario_and_rejects_invalid_replacement():
    generation = PytestGeneration(
        CONTRACT, {"model": "test-model"}, Journal(None, "test")
    )
    save_cases = generation.as_tool()
    case = {
        "name": "create_then_read",
        "category": "scenario",
        "steps": [
            {
                "method": "POST",
                "path": "/items",
                "status": 201,
                "body": {"name": "'; raise RuntimeError('untrusted')"},
            },
            {
                "method": "GET",
                "path": "/items/{itemId}",
                "status": 200,
                "previous_id_param": "itemId",
            },
        ],
    }

    assert "Подготовлено pytest-кейсов: 1" in save_cases.invoke({"cases": [case]})
    assert generation.count == 1
    (file,) = generation.files
    assert file.kind == "pytest"
    code = compile(file.content, file.path, "exec")
    namespace = {}
    exec(code, namespace)
    steps = namespace["CASES"][0]["steps"]
    assert steps[0]["required"] == ["id"]
    assert steps[0]["body"] == case["steps"][0]["body"]
    assert steps[1]["previous_id_param"] == "itemId"

    bad_case = json.loads(json.dumps(case))
    bad_case["steps"][1]["previous_id_param"] = "id"
    assert "укажи ровно ['itemId']" in save_cases.invoke({"cases": [bad_case]})
    assert generation.files == [file]


@pytest.mark.parametrize(
    "path, value, status",
    [
        ("/items/{itemId}", "../etc", 200),
        ("/items/{itemId}", "42", 500),
        ("https://example.test/items/{itemId}", "42", 200),
    ],
)
def test_rejects_undocumented_or_invalid_requests(path, value, status):
    generation = PytestGeneration(CONTRACT, {}, Journal(None, "test"))
    result = generation.as_tool().invoke(
        {
            "cases": [
                {
                    "name": "read_item",
                    "category": "normal",
                    "steps": [
                        {
                            "method": "GET",
                            "path": path,
                            "status": status,
                            "path_params": {"itemId": value},
                        }
                    ],
                }
            ]
        }
    )
    assert "отклонён" in result
    assert generation.files == []
