import json
from typing import Any

PETSTORE: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore API", "version": "1.0.0"},
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "tags": ["pets"],
                "responses": {"200": {"description": "ok"}, "default": {"description": "err"}},
            },
            "post": {
                "operationId": "createPet",
                "tags": ["pets"],
                "responses": {"201": {"description": "created"}, "422": {"description": "bad"}},
            },
        },
        "/pets/{petId}": {
            "parameters": [{"name": "petId", "in": "path", "required": True}],
            "get": {
                "operationId": "getPet",
                "tags": ["pets"],
                "responses": {"200": {"description": "ok"}, "404": {"description": "nf"}},
            },
            "delete": {
                "operationId": "deletePet",
                "tags": ["admin"],
                "responses": {"204": {"description": "gone"}},
            },
        },
    },
}

PETSTORE_JSON = json.dumps(PETSTORE)


def inline_contract() -> dict[str, Any]:
    return {"kind": "inline", "format": "json", "content": PETSTORE_JSON}
