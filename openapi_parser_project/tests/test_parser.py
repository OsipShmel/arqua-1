from pathlib import Path
import sys


sys.path.insert(
    0,
    str(Path(__file__).parent.parent)
)
from openapi_parser import OpenAPIParser


def test_parse_openapi():
    parser = OpenAPIParser()

    file_path = (
        Path(__file__).parent.parent
        / "examples"
        / "pets.yaml"
    )

    contract = parser.parse(file_path)

    assert contract.openapi_version == "3.0.3"
    assert contract.title == "Pet API"

    assert len(contract.operations) == 3

    get_pets = next(
        operation
        for operation in contract.operations
        if operation.operation_id == "getPets"
    )

    assert get_pets.method == "GET"
    assert get_pets.path == "/pets"

    limit = next(
        parameter
        for parameter in get_pets.parameters
        if parameter.name == "limit"
    )

    assert limit.schema_ is not None
    assert limit.schema_.type == "integer"
    assert limit.schema_.minimum == 1
    assert limit.schema_.maximum == 100


def test_parse_components():
    parser = OpenAPIParser()

    file_path = (
        Path(__file__).parent.parent
        / "examples"
        / "pets.yaml"
    )

    contract = parser.parse(file_path)

    assert "Pet" in contract.schemas
    assert "CreatePet" in contract.schemas

    pet = contract.schemas["Pet"]

    assert pet.type == "object"
    assert "id" in pet.properties
    assert "name" in pet.properties

    assert pet.properties["name"].min_length == 1
    assert pet.properties["name"].max_length == 100