"""Contract-level mutants: what to break in a response, and how to break it.

A mutant targets one documented response (operation + status key) and changes it in
one way: a field's type, a missing required field, or a different status code.
Field locations are JSON Pointers where `*` stands for every array element.
"""

import json
import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from testing_tools.api.schemas import MutationOperator, OperationRef
from testing_tools.app.contract import ContractOperation
from testing_tools.app.tools.common import documented_key, resolve

MAX_DEPTH = 4
"""How deep into nested objects/arrays fields are mutated."""

STATUS_SWAP = {"2": 500, "3": 500, "4": 200, "5": 200}
TYPE_SWAP: dict[str, Any] = {
    "string": 12345,
    "integer": "mutated",
    "number": "mutated",
    "boolean": "true",
    "array": {"mutated": True},
    "object": "mutated",
}


@dataclass(frozen=True)
class MutantSpec:
    id: str
    operator: MutationOperator
    operation: ContractOperation
    status_key: str
    pointer: str | None = None
    declared_type: str | None = None
    new_status: int | None = None

    @property
    def description(self) -> str:
        match self.operator:
            case MutationOperator.CHANGE_FIELD_TYPE:
                new = type(TYPE_SWAP[self.declared_type or "object"]).__name__
                return f"{self.declared_type} -> {new} in {self.pointer}"
            case MutationOperator.REMOVE_REQUIRED_FIELD:
                return f"removed required field {self.pointer}"
            case MutationOperator.REPLACE_STATUS_CODE:
                return f"status {self.status_key} -> {self.new_status}"

    @property
    def ref(self) -> OperationRef:
        return self.operation.ref

    def targets(self, op: ContractOperation | None, status: int) -> bool:
        return (
            op is not None
            and op.ref == self.operation.ref
            and documented_key(op, status) == self.status_key
        )

    def apply(self, status: int, body: bytes) -> tuple[int, bytes] | None:
        """The mutated (status, body), or None if this response has nothing to mutate."""
        if self.operator == MutationOperator.REPLACE_STATUS_CODE:
            assert self.new_status is not None
            return self.new_status, body
        assert self.pointer is not None
        try:
            doc = json.loads(body)
        except ValueError:
            return None
        changed = _mutate(doc, _split(self.pointer), self.operator, self.declared_type)
        if not changed:
            return None
        return status, json.dumps(doc).encode()


def generate(
    document: Mapping[str, Any],
    operations: Sequence[ContractOperation],
    operators: Sequence[MutationOperator],
) -> list[MutantSpec]:
    wanted = set(operators)
    specs: list[MutantSpec] = []

    def add(**kwargs: Any) -> None:
        specs.append(MutantSpec(id=f"m-{len(specs) + 1:04d}", **kwargs))

    for op in operations:
        responses = _operation(document, op).get("responses") or {}
        for key in op.status_codes:
            if MutationOperator.REPLACE_STATUS_CODE in wanted and key[0] in STATUS_SWAP:
                add(
                    operator=MutationOperator.REPLACE_STATUS_CODE,
                    operation=op,
                    status_key=key,
                    new_status=STATUS_SWAP[key[0]],
                )
            schema = _json_schema(document, responses.get(key))
            if schema is None:
                continue
            for pointer, declared, required in _fields(document, schema, "", 0, set()):
                if MutationOperator.REMOVE_REQUIRED_FIELD in wanted and required:
                    add(
                        operator=MutationOperator.REMOVE_REQUIRED_FIELD,
                        operation=op,
                        status_key=key,
                        pointer=pointer,
                    )
                if MutationOperator.CHANGE_FIELD_TYPE in wanted and declared in TYPE_SWAP:
                    add(
                        operator=MutationOperator.CHANGE_FIELD_TYPE,
                        operation=op,
                        status_key=key,
                        pointer=pointer,
                        declared_type=declared,
                    )
    return specs


def sample(specs: list[MutantSpec], limit: int | None, seed: int) -> list[MutantSpec]:
    if limit is None or len(specs) <= limit:
        return specs
    chosen = set(random.Random(seed).sample(range(len(specs)), limit))
    return [spec for i, spec in enumerate(specs) if i in chosen]


# --------------------------------------------------------------------------- #
# Schema walking
# --------------------------------------------------------------------------- #


def _operation(document: Mapping[str, Any], op: ContractOperation) -> dict[str, Any]:
    item = resolve(document, (document.get("paths") or {}).get(op.ref.path) or {})
    found = item.get(op.ref.method.value.lower()) if isinstance(item, dict) else None
    return found if isinstance(found, dict) else {}


def _json_schema(document: Mapping[str, Any], response: Any) -> Any:
    response = resolve(document, response)
    content = response.get("content") if isinstance(response, dict) else None
    if not isinstance(content, dict):
        return None
    for media_type, media in content.items():
        if "json" in media_type and isinstance(media, dict) and "schema" in media:
            return media["schema"]
    return None


def _fields(
    document: Mapping[str, Any], schema: Any, pointer: str, depth: int, seen: set[int]
) -> Iterator[tuple[str, str | None, bool]]:
    """(pointer, declared type, is required) for every field reachable from `schema`."""
    schema = _merged(document, schema)
    if depth >= MAX_DEPTH or id(schema) in seen:
        return
    seen = seen | {id(schema)}
    if _type(schema) == "array" and "items" in schema:
        yield from _fields(document, schema["items"], f"{pointer}/*", depth + 1, seen)
        return
    required = set(schema.get("required") or [])
    for name, sub in (schema.get("properties") or {}).items():
        child = f"{pointer}/{_escape(name)}"
        yield child, _type(_merged(document, sub)), name in required
        yield from _fields(document, sub, child, depth + 1, seen)


def _merged(document: Mapping[str, Any], schema: Any) -> dict[str, Any]:
    """Resolve refs and fold `allOf` into one object schema."""
    schema = resolve(document, schema)
    if not isinstance(schema, dict):
        return {}
    if "allOf" not in schema:
        return schema
    merged: dict[str, Any] = {k: v for k, v in schema.items() if k != "allOf"}
    properties = dict(merged.get("properties") or {})
    required = list(merged.get("required") or [])
    for part in schema["allOf"]:
        part = _merged(document, part)
        properties.update(part.get("properties") or {})
        required += part.get("required") or []
        merged.setdefault("type", part.get("type"))
    merged["properties"] = properties
    merged["required"] = required
    return merged


def _type(schema: dict[str, Any]) -> str | None:
    declared = schema.get("type")
    if isinstance(declared, list):  # OpenAPI 3.1: ["string", "null"]
        declared = next((t for t in declared if t != "null"), None)
    if declared is None and "properties" in schema:
        return "object"
    return declared if isinstance(declared, str) else None


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #


def _escape(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


def _split(pointer: str) -> list[str]:
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]


def _mutate(node: Any, parts: list[str], operator: MutationOperator, declared: str | None) -> bool:
    head, rest = parts[0], parts[1:]
    if head == "*":
        if not isinstance(node, list):
            return False
        if rest:
            return any([_mutate(item, rest, operator, declared) for item in node])
        return False
    if not isinstance(node, dict) or head not in node:
        return False
    if rest:
        return _mutate(node[head], rest, operator, declared)
    if operator == MutationOperator.REMOVE_REQUIRED_FIELD:
        del node[head]
    else:
        node[head] = TYPE_SWAP[declared or "object"]
    return True
