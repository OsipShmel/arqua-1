from openapi_parser import OpenAPIParser


parser = OpenAPIParser()

contract = parser.parse(
    "examples/pets.yaml"
)

print(contract.title)
print(contract.openapi_version)

for operation in contract.operations:
    print(
        operation.method,
        operation.path,
        operation.operation_id,
    )