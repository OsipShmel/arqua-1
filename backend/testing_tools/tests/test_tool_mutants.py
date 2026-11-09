import json

from testing_tools.api.schemas import HttpMethod, MutationOperator, OperationRef
from testing_tools.app.contract import ContractOperation, LoadedContract
from testing_tools.app.tools.mutation.mutants import generate, sample

ALL = list(MutationOperator)


def by_description(contract: LoadedContract) -> dict[str, str]:
    specs = generate(contract.document, contract.operations, ALL)
    return {
        f"{s.ref.method.value} {s.ref.path} {s.status_key}: {s.description}": s.id for s in specs
    }


def test_generates_every_operator_per_documented_response(contract: LoadedContract) -> None:
    described = by_description(contract)

    assert "GET /pets 200: status 200 -> 500" in described
    assert "GET /pets 200: removed required field /*/id" in described
    assert "GET /pets 200: integer -> str in /*/id" in described
    assert "GET /pets 200: string -> int in /*/tag" in described
    assert "GET /pets 200: removed required field /*/tag" not in described  # optional
    assert "POST /pets 422: status 422 -> 200" in described  # no body schema, status only
    assert "GET /pets/{petId} 404: removed required field /detail" in described
    assert len(described) == 22
    ids = list(described.values())
    assert ids == [f"m-{i:04d}" for i in range(1, 23)]


def test_operator_and_operation_selection(contract: LoadedContract) -> None:
    [list_pets, *_] = contract.operations

    specs = generate(contract.document, [list_pets], [MutationOperator.REPLACE_STATUS_CODE])

    assert [s.description for s in specs] == ["status 200 -> 500"]


def test_all_of_and_nested_objects_are_walked() -> None:
    doc = {
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {"$ref": "#/components/schemas/Base"},
                                            {
                                                "properties": {
                                                    "owner": {
                                                        "type": "object",
                                                        "required": ["email"],
                                                        "properties": {
                                                            "email": {"type": ["string", "null"]}
                                                        },
                                                    }
                                                }
                                            },
                                        ]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Base": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "integer"}},
                }
            }
        },
    }
    op = ContractOperation(
        ref=OperationRef(method=HttpMethod.GET, path="/x"), tags=(), status_codes=("200",)
    )
    descriptions = {s.description for s in generate(doc, [op], ALL)}

    assert {
        "removed required field /id",
        "integer -> str in /id",
        "object -> str in /owner",
        "removed required field /owner/email",
        "string -> int in /owner/email",
    } <= descriptions


def test_apply_changes_only_matching_fields(contract: LoadedContract) -> None:
    specs = {s.description: s for s in generate(contract.document, contract.operations[:1], ALL)}
    body = json.dumps([{"id": 1, "name": "a"}, {"id": 2, "name": "b", "tag": "t"}]).encode()

    status, out = specs["integer -> str in /*/id"].apply(200, body) or (0, b"")
    assert status == 200
    assert [p["id"] for p in json.loads(out)] == ["mutated", "mutated"]

    _, out = specs["removed required field /*/name"].apply(200, body) or (0, b"")
    assert all("name" not in p for p in json.loads(out))

    _, out = specs["string -> int in /*/tag"].apply(200, body) or (0, b"")
    assert json.loads(out)[1]["tag"] == 12345

    assert specs["string -> int in /*/tag"].apply(200, b"[]") is None
    assert specs["string -> int in /*/tag"].apply(200, b"not json") is None
    assert specs["status 200 -> 500"].apply(200, body) == (500, body)


def test_targets_match_operation_and_status_key(contract: LoadedContract) -> None:
    [list_pets, create_pet, *_] = contract.operations
    [spec] = generate(contract.document, [list_pets], [MutationOperator.REPLACE_STATUS_CODE])

    assert spec.targets(list_pets, 200)
    assert not spec.targets(list_pets, 404)
    assert not spec.targets(create_pet, 200)
    assert not spec.targets(None, 200)


def test_sample_is_deterministic_and_keeps_order(contract: LoadedContract) -> None:
    specs = generate(contract.document, contract.operations, ALL)

    picked = sample(specs, 5, seed=3)

    assert len(picked) == 5
    assert picked == sample(specs, 5, seed=3)
    assert [s.id for s in picked] == sorted(s.id for s in picked)
    assert sample(specs, None, seed=3) == specs
    assert sample(specs, 100, seed=3) == specs
