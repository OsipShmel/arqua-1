import json

from petstore import PETSTORE_JSON
from testing_tools.api.schemas import HttpMethod, HttpRequestSnapshot
from testing_tools.app.contract import LoadedContract, parse_contract
from testing_tools.app.tools.common import (
    BODY_LIMIT,
    Masker,
    Observations,
    OperationIndex,
    build_coverage,
    curl,
    documented_key,
    filtered_document,
    resolve,
    truncate,
)


def petstore() -> LoadedContract:
    return parse_contract(PETSTORE_JSON)


def test_index_matches_concrete_paths_and_labels() -> None:
    index = OperationIndex(petstore().operations)

    get_pet = index.match("get", "/pets/42")
    assert get_pet is not None and get_pet.ref.operation_id == "getPet"
    list_pets = index.match("GET", "/pets/")
    assert list_pets is not None and list_pets.ref.operation_id == "listPets"
    assert index.match("PUT", "/pets/42") is None
    assert index.match("GET", "/pets/42/toys") is None
    label = index.by_label("DELETE /pets/{petId}")
    assert label is not None and label.ref.operation_id == "deletePet"
    assert index.by_label("BREW /coffee") is None


def test_literal_path_wins_over_template() -> None:
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/pets/{petId}": {"get": {"operationId": "byId", "responses": {}}},
            "/pets/mine": {"get": {"operationId": "mine", "responses": {}}},
        },
    }
    index = OperationIndex(parse_contract(json.dumps(doc)).operations)

    op = index.match("GET", "/pets/mine")
    assert op is not None and op.ref.operation_id == "mine"


def test_documented_key_prefers_exact_then_range_then_default() -> None:
    [list_pets, create_pet, get_pet, _] = petstore().operations
    assert documented_key(list_pets, 200) == "200"
    assert documented_key(list_pets, 503) == "default"
    assert documented_key(get_pet, 500) is None
    ranged = create_pet.__class__(ref=create_pet.ref, tags=(), status_codes=("201", "4XX"))
    assert documented_key(ranged, 404) == "4XX"


def test_coverage_counts_documented_and_undocumented_codes() -> None:
    contract = petstore()
    [list_pets, create_pet, get_pet, _] = contract.operations
    observed = Observations()
    for op, status in [(list_pets, 200), (list_pets, 500), (get_pet, 404), (get_pet, 422)]:
        observed.add(op, status)
    observed.add(create_pet, None)  # request without a response (network error)

    coverage = build_coverage(contract.operations, observed)

    assert (coverage.operations_total, coverage.operations_tested) == (4, 3)
    # documented: 200+default, 201+422, 200+404, 204 = 7; covered: 200, default (via 500), 404
    assert (coverage.status_codes_documented, coverage.status_codes_observed) == (7, 3)
    assert coverage.undocumented_status_codes_observed == 1  # 422 on getPet
    assert coverage.by_operation[0].observed_status_codes == [200, 500]
    assert coverage.by_operation[1].tested is True
    assert coverage.by_operation[1].observed_status_codes == []
    assert coverage.by_operation[3].requests == 0


def test_filtered_document_keeps_only_selected_operations() -> None:
    contract = petstore()
    [list_pets, _, get_pet, _] = contract.operations

    doc = filtered_document(contract, [list_pets, get_pet])

    assert list(doc["paths"]["/pets"]) == ["get"]
    assert set(doc["paths"]["/pets/{petId}"]) == {"parameters", "get"}
    assert "post" in contract.document["paths"]["/pets"]  # original untouched


def test_filtered_document_drops_empty_paths() -> None:
    contract = petstore()

    doc = filtered_document(contract, [contract.operations[0]])

    assert list(doc["paths"]) == ["/pets"]


def test_resolve_follows_local_refs() -> None:
    doc = {"components": {"schemas": {"A": {"$ref": "#/components/schemas/B"}, "B": {"x": 1}}}}

    assert resolve(doc, {"$ref": "#/components/schemas/A"}) == {"x": 1}
    assert resolve(doc, {"$ref": "other.yaml#/X"}) == {"$ref": "other.yaml#/X"}
    assert resolve(doc, {"$ref": "#/missing"}) == {"$ref": "#/missing"}


def test_masker_hides_secret_and_auth_headers() -> None:
    masker = Masker(["X-Api-Key"])

    assert masker.headers({"x-api-key": "s", "Authorization": "t", "Accept": "a"}) == {
        "x-api-key": "***",
        "Authorization": "***",
        "Accept": "a",
    }


def test_truncate_and_curl() -> None:
    assert truncate(b"\xff ok") == "� ok"
    long = truncate("x" * (BODY_LIMIT + 10))
    assert long is not None and long.endswith(f"[truncated, {BODY_LIMIT + 10} chars]")

    request = HttpRequestSnapshot(
        method=HttpMethod.POST,
        url="http://h/pets",
        headers={"Content-Type": "application/json"},
        body='{"name": "it\'s"}',
    )
    assert curl(request) == (
        "curl -X POST -H 'Content-Type: application/json' "
        "-d '{\"name\": \"it'\"'\"'s\"}' http://h/pets"
    )
