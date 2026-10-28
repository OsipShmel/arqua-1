from pathlib import Path
import json
from unittest.mock import patch, MagicMock

from openapi_parser import OpenAPIParser
from openapi_parser.loader import OpenAPILoadError
from openapi_parser.parser import OpenAPIParseError


def _pets_path() -> Path:
    return Path(__file__).parent.parent / "examples" / "pets.yaml"


def test_parse_openapi():
    parser = OpenAPIParser()
    contract = parser.parse(_pets_path())

    assert contract.openapi_version == "3.0.3"
    assert contract.title == "Pet API"
    assert len(contract.operations) == 3

    get_pets = next(
        op for op in contract.operations if op.operation_id == "getPets"
    )
    assert get_pets.method == "GET"
    assert get_pets.path == "/pets"

    limit = next(p for p in get_pets.parameters if p.name == "limit")
    assert limit.schema_ is not None
    assert limit.schema_.type == "integer"
    assert limit.schema_.minimum == 1
    assert limit.schema_.maximum == 100


def test_parse_components():
    parser = OpenAPIParser()
    contract = parser.parse(_pets_path())

    assert "Pet" in contract.schemas
    assert "CreatePet" in contract.schemas

    pet = contract.schemas["Pet"]
    assert pet.type == "object"
    assert "id" in pet.properties
    assert "name" in pet.properties
    assert pet.properties["name"].min_length == 1
    assert pet.properties["name"].max_length == 100


def test_stats():
    parser = OpenAPIParser()
    contract = parser.parse(_pets_path())

    assert contract.stats.title == "Pet API"
    assert contract.stats.api_version == "1.0.0"
    assert contract.stats.operations_count == 3
    assert contract.stats.paths_count == 2
    assert contract.stats.schemas_count == 2


def test_ref_resolution():
    parser = OpenAPIParser()
    contract = parser.parse(_pets_path())

    # В /pets/{petId} параметр petId лежит на уровне path и должен
    # разрезолвиться в schema integer
    get_pet = next(
        op for op in contract.operations if op.operation_id == "getPet"
    )
    pet_id = next(p for p in get_pet.parameters if p.name == "petId")
    assert pet_id.schema_ is not None
    assert pet_id.schema_.type == "integer"

    # $ref в ответе getPets должен разрезолвиться в Pet
    get_pets = next(
        op for op in contract.operations if op.operation_id == "getPets"
    )
    resp_200 = get_pets.responses["200"]
    schema = resp_200.content["application/json"]
    assert schema.type == "array"
    assert schema.items is not None
    assert schema.items.resolved_ref == "#/components/schemas/Pet"


def test_summary_structure():
    parser = OpenAPIParser()
    contract = parser.parse(_pets_path())

    summary = contract.summary
    assert summary["title"] == "Pet API"
    assert len(summary["operations"]) == 3

    get_pets = next(
        op for op in summary["operations"] if op["operation_id"] == "getPets"
    )
    assert get_pets["method"] == "GET"
    assert get_pets["path"] == "/pets"
    assert any(p["name"] == "limit" for p in get_pets["parameters"])
    assert "200" in get_pets["responses"]


def test_parse_url(monkeypatch):
    parser = OpenAPIParser()
    yaml_text = _pets_path().read_text(encoding="utf-8")

    class FakeResponse:
        def read(self):
            return yaml_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("openapi_parser.loader.urlopen", return_value=FakeResponse()):
        contract = parser.parse("https://example.com/pets.yaml")

    assert contract.title == "Pet API"
    assert contract.stats.source == "https://example.com/pets.yaml"


def test_invalid_document_error():
    parser = OpenAPIParser()
    with pytest.raises(OpenAPIParseError):
        parser.parse({"openapi": "3.0.3"})  # нет info и paths


def test_swagger_2_rejected():
    parser = OpenAPIParser()
    with pytest.raises(OpenAPIParseError):
        parser.parse({"swagger": "2.0", "info": {}, "paths": {}})


import pytest  # noqa: E402