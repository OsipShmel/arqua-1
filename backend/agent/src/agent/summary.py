"""Operation-oriented OpenAPI context; never forward the complete input to the LLM."""

from __future__ import annotations

import json

METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
SCHEMA_KEYS = {
    "type",
    "format",
    "title",
    "description",
    "nullable",
    "required",
    "enum",
    "const",
    "default",
    "example",
    "examples",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "readOnly",
    "writeOnly",
    "deprecated",
    "discriminator",
    "dependentRequired",
    "contentEncoding",
    "contentMediaType",
}


class Summarizer:
    def __init__(self, document: dict):
        self.document = document
        self.schemas: dict = {}
        self.dependencies: list = []

    def pointer(self, reference: str):
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError(f"Поддерживаются только локальные $ref: {reference}")
        target = self.document
        try:
            for segment in reference[2:].split("/"):
                key = segment.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Не найдено определение $ref: {reference}") from exc
        return target

    def object(self, value, seen: frozenset = frozenset()) -> dict:
        if not isinstance(value, dict):
            raise ValueError("Ожидался объект OpenAPI")
        if "$ref" not in value:
            return value
        ref = value["$ref"]
        if not isinstance(ref, str) or ref in seen:
            raise ValueError("Циклический $ref вне схемы")
        target = self.object(self.pointer(ref), seen | {ref})
        return {**target, **{k: v for k, v in value.items() if k != "$ref"}}

    def schema(self, value, depth: int = 0):
        if isinstance(value, bool):
            return value
        if depth > 80 or not isinstance(value, dict):
            raise ValueError("Некорректная или слишком глубокая схема OpenAPI")
        if "$ref" in value:
            ref = value["$ref"]
            if not isinstance(ref, str):
                raise ValueError("$ref должен быть строкой")
            if ref not in self.schemas:
                self.schemas[ref] = (
                    None  # Register before traversal, including recursive schemas.
                )
                self.schemas[ref] = self.schema(self.pointer(ref), depth + 1)
            siblings = {k: v for k, v in value.items() if k != "$ref"}
            reference = {"schema_ref": ref}
            return (
                {"allOf": [reference, self.schema(siblings, depth + 1)]}
                if siblings
                else reference
            )
        result = {k: v for k, v in value.items() if k in SCHEMA_KEYS}
        for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            if key in value:
                result[key] = {
                    k: self.schema(v, depth + 1) for k, v in value[key].items()
                }
        for key in (
            "items",
            "additionalProperties",
            "unevaluatedProperties",
            "contains",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
        ):
            if key in value:
                result[key] = self.schema(value[key], depth + 1)
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            if key in value:
                result[key] = [self.schema(v, depth + 1) for v in value[key]]
        return result

    def examples(self, value: dict) -> dict:
        result = {}
        if "example" in value:
            result["example"] = value["example"]
        if "examples" in value:
            result["examples"] = {}
            for name, example in value["examples"].items():
                example = self.object(example)
                result["examples"][name] = {
                    k: example[k]
                    for k in ("value", "summary", "externalValue")
                    if k in example
                }
        return result

    def content(self, value: dict) -> dict:
        result = {}
        for media_type, media in value.items():
            item = self.examples(media)
            if "schema" in media:
                item["schema"] = self.schema(media["schema"])
            if "encoding" in media:
                item["encoding"] = media["encoding"]
            result[media_type] = item
        return result

    def parameter(self, value: dict) -> dict:
        value = self.object(value)
        result = {
            k: value[k]
            for k in (
                "name",
                "in",
                "required",
                "description",
                "deprecated",
                "style",
                "explode",
                "allowReserved",
                "allowEmptyValue",
            )
            if k in value
        }
        result.update(self.examples(value))
        if "schema" in value:
            result["schema"] = self.schema(value["schema"])
        if "content" in value:
            result["content"] = self.content(value["content"])
        return result

    def response(self, raw: dict, source: dict) -> dict:
        value = self.object(raw)
        result = {"description": value.get("description", "")}
        if "content" in value:
            result["content"] = self.content(value["content"])
        if "headers" in value:
            result["headers"] = {
                k: self.parameter(v) for k, v in value["headers"].items()
            }
        for name, link in value.get("links", {}).items():
            link = self.object(link)
            self.dependencies.append(
                {
                    "source": source,
                    "name": name,
                    **{
                        k: link[k]
                        for k in (
                            "operationId",
                            "operationRef",
                            "parameters",
                            "requestBody",
                        )
                        if k in link
                    },
                }
            )
        return result

    def build(self) -> dict:
        operations = []
        for path, raw_path in self.document["paths"].items():
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            path_item = self.object(raw_path)
            for method, operation in path_item.items():
                if method not in METHODS:
                    continue
                operation = self.object(operation)
                item = {"method": method.upper(), "path": path}
                item.update(
                    {
                        k: operation[k]
                        for k in ("operationId", "summary", "description", "deprecated")
                        if k in operation
                    }
                )
                parameters = {}
                for raw in [
                    *path_item.get("parameters", []),
                    *operation.get("parameters", []),
                ]:
                    parameter = self.parameter(raw)
                    parameters[(parameter.get("in"), parameter.get("name"))] = parameter
                if parameters:
                    item["parameters"] = list(parameters.values())
                if "requestBody" in operation:
                    body = self.object(operation["requestBody"])
                    item["request_body"] = {
                        "required": body.get("required", False),
                        "content": self.content(body.get("content", {})),
                    }
                item["responses"] = {
                    str(code): self.response(raw, item_ref(item, code))
                    for code, raw in operation.get("responses", {}).items()
                }
                item["security"] = operation.get(
                    "security", self.document.get("security", [])
                )
                if "servers" in operation or "servers" in path_item:
                    item["servers"] = operation.get("servers", path_item.get("servers"))
                operations.append(item)
        info = self.document["info"]
        result = {
            "summary_version": 1,
            "api": {
                "title": info["title"],
                "version": str(info["version"]),
                "openapi": self.document["openapi"],
            },
            "operations": operations,
            "schemas": self.schemas,
            "dependencies": self.dependencies,
        }
        used = {
            name
            for op in operations
            for requirement in op["security"]
            for name in requirement
        }
        schemes = self.document.get("components", {}).get("securitySchemes", {})
        result["security_schemes"] = {
            name: self.object(schemes[name]) for name in sorted(used) if name in schemes
        }
        return result


def item_ref(operation: dict, code) -> dict:
    return {
        "method": operation["method"],
        "path": operation["path"],
        "status_code": str(code),
    }


def summarize(document: dict, max_chars: int = 100_000) -> dict:
    """Keep all operations or reject explicitly; never silently truncate coverage."""
    result = Summarizer(document).build()
    size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if size > max_chars:
        raise ValueError(
            f"Summary содержит {size} символов, лимит {max_chars}; увеличьте --summary-max-chars"
        )
    return result
