import copy
import json

import pytest

from agent.summary import summarize


def contract():
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Demo",
            "version": "1",
            "contact": {"email": "omit@private.test"},
        },
        "security": [{"auth": []}],
        "paths": {
            "/items/{id}": {
                "parameters": [{"$ref": "#/components/parameters/Id"}],
                "get": {"responses": {"200": {"$ref": "#/components/responses/Node"}}},
                "post": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "id",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 10},
                        }
                    ],
                    "requestBody": {"$ref": "#/components/requestBodies/Body"},
                    "responses": {
                        "201": {
                            "description": "created",
                            "links": {
                                "next": {
                                    "operationId": "getItem",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "parameters": {
                "Id": {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer", "minimum": 1},
                }
            },
            "responses": {
                "Node": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Node"},
                            "examples": {
                                "first": {"$ref": "#/components/examples/First"}
                            },
                        }
                    },
                }
            },
            "requestBodies": {
                "Body": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {
                                        "type": ["string", "null"],
                                        "default": None,
                                        "pattern": "^[a-z]+$",
                                        "minLength": 1,
                                    }
                                },
                            }
                        }
                    },
                }
            },
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "multipleOf": 5},
                        "child": {"$ref": "#/components/schemas/Node"},
                    },
                },
                "Unused": {"description": "never send this"},
            },
            "examples": {"First": {"value": {"id": 5}}},
            "securitySchemes": {"auth": {"type": "http", "scheme": "bearer"}},
        },
    }


def test_normalization_refs_constraints_examples_overrides_and_dependencies():
    document = contract()
    original = copy.deepcopy(document)
    summary = summarize(document)
    get, post = summary["operations"]
    assert get["parameters"][0]["schema"]["minimum"] == 1
    assert post["parameters"][0]["schema"]["minimum"] == 10
    assert post["request_body"]["required"] is True
    name = post["request_body"]["content"]["application/json"]["schema"]["properties"][
        "name"
    ]
    assert name["default"] is None and name["pattern"] == "^[a-z]+$"
    assert get["responses"]["200"]["content"]["application/json"]["examples"]["first"][
        "value"
    ] == {"id": 5}
    assert summary["schemas"]["#/components/schemas/Node"]["properties"]["child"] == {
        "schema_ref": "#/components/schemas/Node"
    }
    assert summary["security_schemes"]["auth"]["scheme"] == "bearer"
    assert summary["dependencies"][0]["operationId"] == "getItem"
    assert "never send this" not in json.dumps(
        summary
    ) and "omit@private.test" not in json.dumps(summary)
    assert document == original


@pytest.mark.parametrize(
    "reference", ["https://example.test/schema.json", "#/components/schemas/Missing"]
)
def test_unsupported_and_missing_refs_are_explicit(reference):
    document = contract()
    document["components"]["schemas"]["Node"] = {"$ref": reference}
    with pytest.raises(ValueError, match=r"\$ref"):
        summarize(document)


def test_summary_limit_rejects_instead_of_dropping_operations():
    with pytest.raises(ValueError, match="summary-max-chars"):
        summarize(contract(), max_chars=10)


def test_boolean_schema_and_ref_siblings_are_kept():
    document = contract()
    document["components"]["schemas"]["Node"] = False
    document["components"]["responses"]["Node"]["content"]["application/json"][
        "schema"
    ]["description"] = "sibling"
    summary = summarize(document)
    assert summary["schemas"]["#/components/schemas/Node"] is False
    schema = summary["operations"][0]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["allOf"][1]["description"] == "sibling"
