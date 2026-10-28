import hashlib

import httpx2
import pytest

from petstore import PETSTORE_JSON
from testing_tools.api.schemas import (
    ContractFormat,
    ErrorCode,
    HttpMethod,
    InlineContractSource,
    OperationFilter,
    OperationMatcher,
    UrlContractSource,
)
from testing_tools.app.contract import (
    LoadedContract,
    load_contract,
    parse_contract,
    select_operations,
)
from testing_tools.app.errors import ApiError

PETSTORE_YAML = """
openapi: 3.1.0
info: {title: Yaml Store, version: "2.0"}
paths:
  /items:
    get:
      responses:
        200: {description: ok}
        4XX: {description: client error}
"""


def _ops(contract: LoadedContract) -> list[tuple[str, str]]:
    return [(op.ref.method.value, op.ref.path) for op in contract.operations]


def test_parse_json_contract() -> None:
    contract = parse_contract(PETSTORE_JSON)

    assert contract.info.title == "Petstore API"
    assert contract.info.version == "1.0.0"
    assert contract.info.openapi_version == "3.0.3"
    assert contract.info.operations_total == 4
    assert contract.info.sha256 == hashlib.sha256(PETSTORE_JSON.encode()).hexdigest()
    assert _ops(contract) == [
        ("GET", "/pets"),
        ("POST", "/pets"),
        ("GET", "/pets/{petId}"),
        ("DELETE", "/pets/{petId}"),
    ]
    get_pet = contract.operations[2]
    assert get_pet.ref.operation_id == "getPet"
    assert get_pet.tags == ("pets",)
    assert get_pet.status_codes == ("200", "404")


def test_parse_yaml_contract_normalizes_status_codes() -> None:
    contract = parse_contract(PETSTORE_YAML)

    assert contract.info.openapi_version == "3.1.0"
    assert contract.operations[0].ref.operation_id is None
    assert contract.operations[0].status_codes == ("200", "4XX")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{not json: [",
        '"just a string"',
        '{"swagger": "2.0", "info": {"title": "t", "version": "1"}, "paths": {}}',
        '{"openapi": "3.0.0", "paths": {}}',
        '{"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": []}',
    ],
)
def test_invalid_contract(text: str) -> None:
    with pytest.raises(ApiError) as exc:
        parse_contract(text)

    assert exc.value.status == 422
    assert exc.value.code == ErrorCode.CONTRACT_INVALID


@pytest.mark.anyio
async def test_load_inline_contract() -> None:
    source = InlineContractSource(format=ContractFormat.YAML, content=PETSTORE_YAML)
    async with httpx2.AsyncClient() as client:
        contract = await load_contract(source, client)

    assert contract.info.title == "Yaml Store"


@pytest.mark.anyio
async def test_load_url_contract_sends_headers() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, text=PETSTORE_JSON)

    source = UrlContractSource(
        url="http://svc:8080/openapi.json", headers={"Authorization": "Bearer t"}
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        contract = await load_contract(source, client)

    assert contract.info.operations_total == 4
    assert str(seen[0].url) == "http://svc:8080/openapi.json"
    assert seen[0].headers["authorization"] == "Bearer t"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["status", "network"])
async def test_url_contract_unreachable(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "network":
            raise httpx2.ConnectError("connection refused", request=request)
        return httpx2.Response(404)

    source = UrlContractSource(url="http://svc:8080/openapi.json")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as exc:
            await load_contract(source, client)

    assert exc.value.status == 422
    assert exc.value.code == ErrorCode.CONTRACT_UNREACHABLE


def _selected(flt: OperationFilter | None) -> list[str | None]:
    contract = parse_contract(PETSTORE_JSON)
    return [op.ref.operation_id for op in select_operations(contract.operations, flt)]


def test_no_filter_selects_everything() -> None:
    assert _selected(None) == ["listPets", "createPet", "getPet", "deletePet"]
    assert _selected(OperationFilter(include=OperationMatcher())) == [
        "listPets",
        "createPet",
        "getPet",
        "deletePet",
    ]


def test_include_fields_are_anded_and_values_ored() -> None:
    flt = OperationFilter(
        include=OperationMatcher(
            path_regex=[r"\{petId\}$", "^/nothing"], methods=[HttpMethod.GET, HttpMethod.POST]
        )
    )

    assert _selected(flt) == ["getPet"]


def test_exclude_wins_over_include() -> None:
    flt = OperationFilter(
        include=OperationMatcher(path_regex=["^/pets"]),
        exclude=OperationMatcher(tags=["admin"]),
    )

    assert _selected(flt) == ["listPets", "createPet", "getPet"]


def test_filter_by_operation_id() -> None:
    flt = OperationFilter(exclude=OperationMatcher(operation_ids=["listPets", "getPet"]))

    assert _selected(flt) == ["createPet", "deletePet"]
