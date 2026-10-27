from openapi_parser import OpenAPIParser


parser = OpenAPIParser()

contract = parser.parse("examples/pets.yaml")

print("=== Stats ===")
print(contract.stats.as_text())

print("\n=== Operations ===")
for operation in contract.operations:
    print(
        operation.method,
        operation.path,
        operation.operation_id,
    )

print("\n=== Summary (for LLM) ===")
import json
print(json.dumps(contract.summary, ensure_ascii=False, indent=2, default=str))