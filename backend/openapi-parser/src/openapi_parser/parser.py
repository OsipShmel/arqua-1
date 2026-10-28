from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import OpenAPILoader, OpenAPILoadError
from .models import (
    ContractStats,
    OpenAPIContract,
    OperationInfo,
    ParameterInfo,
    RequestBodyInfo,
    ResponseInfo,
    SchemaInfo,
    ServerInfo,
)


HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
}

MAX_REF_DEPTH = 50


class OpenAPIParseError(Exception):
    """Понятная ошибка разбора OpenAPI-контракта."""


class OpenAPIParser:
    """
    Parser OpenAPI 3.x.

    На вход:
        - путь к JSON/YAML;
        - URL (http/https);
        - dict с уже загруженным OpenAPI документом.

    На выход:
        OpenAPIContract (со stats и summary для LLM).
    """

    def __init__(self) -> None:
        self.loader = OpenAPILoader()
        self._source: str | None = None
        self._ref_cache: dict[str, dict[str, Any]] = {}
        self._schema_refs: set[str] = set()

    # ---------------------------------------------------------
    # Public
    # ---------------------------------------------------------

    def parse(
        self,
        source: str | Path | dict[str, Any],
    ) -> OpenAPIContract:

        if not isinstance(source, dict):
            self._source = str(source)

        document = self.loader.load(source)

        self._validate_openapi_version(document)
        self._prepare_ref_cache(document)
        self._schema_refs.clear()

        operations = self._parse_operations(document)
        schemas = self._parse_components(document)
        servers = self._parse_servers(document)

        stats = ContractStats(
            source=self._source,
            title=self._get_title(document),
            api_version=self._get_api_version(document),
            openapi_version=document["openapi"],
            spec_type=self._detect_spec_type(document),
            operations_count=len(operations),
            paths_count=len(document.get("paths", {}) or {}),
            schemas_count=len(schemas),
            servers_count=len(servers),
        )

        contract = OpenAPIContract(
            openapi_version=document["openapi"],
            title=stats.title,
            api_version=stats.api_version,
            description=document.get("info", {}).get("description"),
            servers=servers,
            operations=operations,
            schemas=schemas,
            stats=stats,
            raw=document,
        )

        contract.summary = self._build_summary(contract)

        return contract

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------

    def _validate_openapi_version(
        self,
        document: dict[str, Any],
    ) -> None:
        version = document.get("openapi")

        if isinstance(version, str) and version.startswith("2."):
            raise OpenAPIParseError(
                "Swagger 2.0 пока не поддерживается. "
                "Поддерживается OpenAPI 3.x."
            )

        if not isinstance(version, str):
            raise OpenAPIParseError(
                "Отсутствует обязательное поле 'openapi' (строка версии)."
            )

        if not version.startswith("3."):
            raise OpenAPIParseError(
                f"Поддерживается только OpenAPI 3.x. Получено: {version}"
            )

        if "info" not in document or not isinstance(document["info"], dict):
            raise OpenAPIParseError(
                "Документ OpenAPI должен содержать объект 'info'."
            )

        if "paths" not in document or not isinstance(document["paths"], dict):
            raise OpenAPIParseError(
                "Документ OpenAPI должен содержать объект 'paths'."
            )

    def _detect_spec_type(self, document: dict[str, Any]) -> str:
        version = document.get("openapi", "")
        if isinstance(version, str) and version.startswith("3.1"):
            return "OpenAPI 3.1"
        return "OpenAPI 3.x"

    # ---------------------------------------------------------
    # $ref resolution
    # ---------------------------------------------------------

    def _prepare_ref_cache(self, document: dict[str, Any]) -> None:
        self._ref_cache = {"#": document}

    def _resolve_ref(self, ref: str, depth: int = 0) -> dict[str, Any] | None:
        if depth > MAX_REF_DEPTH:
            raise OpenAPIParseError(
                f"Превышена глубина разрешения $ref: {ref}"
            )

        if ref in self._ref_cache:
            return self._ref_cache[ref]

        if not ref.startswith("#/"):
            # внешние $ref не поддерживаем в MVP
            return None

        current: Any = self._ref_cache["#"]
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]

        if isinstance(current, dict):
            self._ref_cache[ref] = current
            return current

        return None

    def _deref(self, node: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        """Раскрывает цепочку $ref, возвращает итоговый dict."""
        seen: set[str] = set()
        current = node
        while isinstance(current, dict) and "$ref" in current:
            ref = current["$ref"]
            if ref in seen:
                raise OpenAPIParseError(
                    f"Циклическая ссылка $ref: {ref}"
                )
            seen.add(ref)
            resolved = self._resolve_ref(ref, depth=depth)
            if resolved is None:
                return current
            current = resolved
            depth += 1
            if depth > MAX_REF_DEPTH:
                raise OpenAPIParseError(
                    f"Слишком глубокая цепочка $ref: {ref}"
                )
        return current

    # ---------------------------------------------------------
    # Info
    # ---------------------------------------------------------

    def _get_title(self, document: dict[str, Any]) -> str:
        title = document.get("info", {}).get("title")
        if not title:
            raise OpenAPIParseError("OpenAPI 'info.title' обязателен.")
        return str(title)

    def _get_api_version(self, document: dict[str, Any]) -> str:
        version = document.get("info", {}).get("version")
        if not version:
            raise OpenAPIParseError("OpenAPI 'info.version' обязателен.")
        return str(version)

    # ---------------------------------------------------------
    # Servers
    # ---------------------------------------------------------

    def _parse_servers(self, document: dict[str, Any]) -> list[ServerInfo]:
        result: list[ServerInfo] = []
        for server in document.get("servers", []) or []:
            if not isinstance(server, dict):
                continue
            result.append(
                ServerInfo(
                    url=server.get("url", ""),
                    description=server.get("description"),
                    variables=server.get("variables", {}) or {},
                )
            )
        return result

    # ---------------------------------------------------------
    # Paths / operations
    # ---------------------------------------------------------

    def _parse_operations(self, document: dict[str, Any]) -> list[OperationInfo]:
        operations: list[OperationInfo] = []
        paths = document.get("paths", {}) or {}
        if not isinstance(paths, dict):
            return operations

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            path_parameters = self._parse_parameters(
                path_item.get("parameters", []) or [],
                location=f"paths.{path}.parameters",
            )

            for method, operation in path_item.items():
                method_lower = method.lower()
                if method_lower not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue

                op_location = f"paths.{path}.{method_lower}"

                operation_parameters = self._parse_parameters(
                    operation.get("parameters", []) or [],
                    location=f"{op_location}.parameters",
                )

                parameters = self._merge_parameters(
                    path_parameters, operation_parameters
                )

                request_body = self._parse_request_body(
                    operation.get("requestBody"),
                    location=f"{op_location}.requestBody",
                )

                responses = self._parse_responses(
                    operation.get("responses", {}) or {},
                    location=f"{op_location}.responses",
                )

                operations.append(
                    OperationInfo(
                        path=path,
                        method=method.upper(),
                        operation_id=operation.get("operationId"),
                        summary=operation.get("summary"),
                        description=operation.get("description"),
                        tags=operation.get("tags", []) or [],
                        parameters=parameters,
                        request_body=request_body,
                        responses=responses,
                        security=operation.get("security"),
                    )
                )

        self._infer_id_links(operations)

        return operations

    def _infer_id_links(self, operations: list[OperationInfo]) -> None:
        """
        ТЗ п.2.4: связи между операциями (id, создаваемый одной операцией
        и используемый другой).
        Эвристика: ищем *_id / id в телах ответов 2xx и тел запросов.
        """
        # Собираем, какие id «отдаёт» операция в ответах 2xx
        producers: dict[str, list[str]] = {}
        consumers: dict[str, list[str]] = {}

        for op in operations:
            for code, resp in op.responses.items():
                if not str(code).startswith("2"):
                    continue
                for schema in resp.content.values():
                    for field in schema.properties:
                        if field == "id" or field.endswith("_id"):
                            producers.setdefault(field, []).append(
                                op.operation_id or f"{op.method} {op.path}"
                            )

            rb = op.request_body
            if rb:
                for schema in rb.content.values():
                    for field in schema.properties:
                        if field == "id" or field.endswith("_id"):
                            consumers.setdefault(field, []).append(
                                op.operation_id or f"{op.method} {op.path}"
                            )

        for op in operations:
            op.produces_ids = sorted(
                {
                    f
                    for ids in producers.values()
                    if (op.operation_id or f"{op.method} {op.path}") in ids
                    for f in [k for k, v in producers.items() if (op.operation_id or f"{op.method} {op.path}") in v]
                }
            )
            op.consumes_ids = sorted(
                {
                    f
                    for ids in consumers.values()
                    if (op.operation_id or f"{op.method} {op.path}") in ids
                    for f in [k for k, v in consumers.items() if (op.operation_id or f"{op.method} {op.path}") in v]
                }
            )

    # ---------------------------------------------------------
    # Parameters
    # ---------------------------------------------------------

    def _parse_parameters(
        self,
        parameters: list[Any],
        location: str = "parameters",
    ) -> list[ParameterInfo]:
        result: list[ParameterInfo] = []
        if not isinstance(parameters, list):
            return result

        for idx, parameter in enumerate(parameters):
            if not isinstance(parameter, dict):
                continue

            parameter = self._deref(parameter)

            name = parameter.get("name")
            loc = parameter.get("in")

            if not name or not loc:
                continue

            schema = None
            if isinstance(parameter.get("schema"), dict):
                schema = self._parse_schema(
                    parameter["schema"],
                    location=f"{location}[{idx}].schema",
                )

            content = self._parse_content(
                parameter.get("content", {}) or {},
                location=f"{location}[{idx}].content",
            )

            result.append(
                ParameterInfo(
                    name=name,
                    **{"in": loc},
                    required=bool(parameter.get("required", False)),
                    description=parameter.get("description"),
                    schema=schema,
                    content=content,
                    example=parameter.get("example"),
                    examples=self._collect_examples(parameter),
                )
            )

        return result

    @staticmethod
    def _collect_examples(node: dict[str, Any]) -> list[Any]:
        examples = node.get("examples")
        if isinstance(examples, dict):
            return list(examples.values())
        if isinstance(examples, list):
            return examples
        return []

    def _merge_parameters(
        self,
        path_parameters: list[ParameterInfo],
        operation_parameters: list[ParameterInfo],
    ) -> list[ParameterInfo]:
        merged: dict[tuple[str, str], ParameterInfo] = {}
        for parameter in path_parameters:
            merged[(parameter.location, parameter.name)] = parameter
        for parameter in operation_parameters:
            merged[(parameter.location, parameter.name)] = parameter
        return list(merged.values())

    # ---------------------------------------------------------
    # Request body
    # ---------------------------------------------------------

    def _parse_request_body(
        self,
        request_body: Any,
        location: str = "requestBody",
    ) -> RequestBodyInfo | None:
        if not isinstance(request_body, dict):
            return None

        request_body = self._deref(request_body)

        return RequestBodyInfo(
            description=request_body.get("description"),
            required=bool(request_body.get("required", False)),
            content=self._parse_content(
                request_body.get("content", {}) or {},
                location=f"{location}.content",
            ),
            example=request_body.get("example"),
        )

    # ---------------------------------------------------------
    # Responses
    # ---------------------------------------------------------

    def _parse_responses(
        self,
        responses: Any,
        location: str = "responses",
    ) -> dict[str, ResponseInfo]:
        result: dict[str, ResponseInfo] = {}
        if not isinstance(responses, dict):
            return result

        for status_code, response in responses.items():
            if not isinstance(response, dict):
                continue

            response = self._deref(response)

            result[str(status_code)] = ResponseInfo(
                status_code=str(status_code),
                description=response.get("description", ""),
                content=self._parse_content(
                    response.get("content", {}) or {},
                    location=f"{location}.{status_code}.content",
                ),
                headers=response.get("headers", {}) or {},
            )

        return result

    # ---------------------------------------------------------
    # Content
    # ---------------------------------------------------------

    def _parse_content(
        self,
        content: Any,
        location: str = "content",
    ) -> dict[str, SchemaInfo]:
        result: dict[str, SchemaInfo] = {}
        if not isinstance(content, dict):
            return result

        for media_type, media_definition in content.items():
            if not isinstance(media_definition, dict):
                continue
            schema = media_definition.get("schema")
            if isinstance(schema, dict):
                result[media_type] = self._parse_schema(
                    schema,
                    location=f"{location}.{media_type}.schema",
                )

        return result

    # ---------------------------------------------------------
    # Components
    # ---------------------------------------------------------

    def _parse_components(self, document: dict[str, Any]) -> dict[str, SchemaInfo]:
        components = document.get("components", {}) or {}
        if not isinstance(components, dict):
            return {}
        schemas = components.get("schemas", {}) or {}
        if not isinstance(schemas, dict):
            return {}

        return {
            name: self._parse_schema(
                schema,
                location=f"components.schemas.{name}",
            )
            for name, schema in schemas.items()
            if isinstance(schema, dict)
        }

    # ---------------------------------------------------------
    # Schema
    # ---------------------------------------------------------

    def _parse_schema(
        self,
        schema: dict[str, Any],
        location: str = "schema",
        depth: int = 0,
    ) -> SchemaInfo:
        # Keep a reference at a recursive edge instead of unfolding it forever.
        ref = schema.get("$ref")
        if ref is None:
            return self._parse_schema_value(schema, location, depth)
        if ref in self._schema_refs:
            return SchemaInfo(ref=ref, resolved_ref=ref)
        self._schema_refs.add(ref)
        try:
            return self._parse_schema_value(schema, location, depth)
        finally:
            self._schema_refs.remove(ref)

    def _parse_schema_value(
        self,
        schema: dict[str, Any],
        location: str = "schema",
        depth: int = 0,
    ) -> SchemaInfo:
        if depth > MAX_REF_DEPTH:
            raise OpenAPIParseError(f"Слишком глубокая вложенность схемы: {location}")

        original_ref = schema.get("$ref")
        resolved_ref = None

        if "$ref" in schema:
            resolved = self._resolve_ref(schema["$ref"])
            if resolved is None:
                return SchemaInfo(ref=schema["$ref"])
            resolved_ref = schema["$ref"]
            schema = resolved
            original_ref = schema.get("$ref")

        schema_type = schema.get("type")
        nullable = bool(schema.get("nullable", False))
        if isinstance(schema_type, list):
            nullable = nullable or ("null" in schema_type)

        properties: dict[str, SchemaInfo] = {}
        raw_properties = schema.get("properties", {}) or {}
        if isinstance(raw_properties, dict):
            for name, prop_schema in raw_properties.items():
                if isinstance(prop_schema, dict):
                    properties[name] = self._parse_schema(
                        prop_schema,
                        location=f"{location}.properties.{name}",
                        depth=depth + 1,
                    )

        items = None
        if isinstance(schema.get("items"), dict):
            items = self._parse_schema(
                schema["items"],
                location=f"{location}.items",
                depth=depth + 1,
            )

        all_of = self._parse_schema_list(
            schema.get("allOf", []) or [],
            location=f"{location}.allOf",
            depth=depth + 1,
        )
        one_of = self._parse_schema_list(
            schema.get("oneOf", []) or [],
            location=f"{location}.oneOf",
            depth=depth + 1,
        )
        any_of = self._parse_schema_list(
            schema.get("anyOf", []) or [],
            location=f"{location}.anyOf",
            depth=depth + 1,
        )

        additional_properties = schema.get("additionalProperties")
        if isinstance(additional_properties, dict):
            additional_properties = self._parse_schema(
                additional_properties,
                location=f"{location}.additionalProperties",
                depth=depth + 1,
            )

        examples_raw = schema.get("examples")
        examples_list: list[Any] = []
        if isinstance(examples_raw, list):
            examples_list = examples_raw
        elif isinstance(examples_raw, dict):
            examples_list = list(examples_raw.values())

        return SchemaInfo(
            ref=original_ref,
            resolved_ref=resolved_ref,
            type=schema_type,
            format=schema.get("format"),
            title=schema.get("title"),
            description=schema.get("description"),
            nullable=nullable,
            required=schema.get("required", []) or [],
            properties=properties,
            items=items,
            enum=schema.get("enum", []) or [],
            default=schema.get("default"),
            example=schema.get("example"),
            examples=examples_list,
            minimum=schema.get("minimum"),
            maximum=schema.get("maximum"),
            exclusive_minimum=schema.get("exclusiveMinimum"),
            exclusive_maximum=schema.get("exclusiveMaximum"),
            min_length=schema.get("minLength"),
            max_length=schema.get("maxLength"),
            min_items=schema.get("minItems"),
            max_items=schema.get("maxItems"),
            unique_items=schema.get("uniqueItems"),
            pattern=schema.get("pattern"),
            all_of=all_of,
            one_of=one_of,
            any_of=any_of,
            additional_properties=additional_properties,
        )

    def _parse_schema_list(
        self,
        schemas: Any,
        location: str = "schemas",
        depth: int = 0,
    ) -> list[SchemaInfo]:
        if not isinstance(schemas, list):
            return []
        return [
            self._parse_schema(
                schema,
                location=f"{location}[{i}]",
                depth=depth,
            )
            for i, schema in enumerate(schemas)
            if isinstance(schema, dict)
        ]

    # ---------------------------------------------------------
    # Summary for LLM (ТЗ п.2.2 / 2.4)
    # ---------------------------------------------------------

    def _build_summary(self, contract: OpenAPIContract) -> dict[str, Any]:
        return {
            "title": contract.title,
            "api_version": contract.api_version,
            "openapi_version": contract.openapi_version,
            "servers": [s.url for s in contract.servers],
            "operations": [
                self._operation_summary(op)
                for op in contract.operations
            ],
        }

    def _operation_summary(self, op: OperationInfo) -> dict[str, Any]:
        return {
            "operation_id": op.operation_id,
            "method": op.method,
            "path": op.path,
            "summary": op.summary,
            "tags": op.tags,
            "parameters": [
                self._parameter_summary(p) for p in op.parameters
            ],
            "request_body": self._request_body_summary(op.request_body),
            "responses": {
                code: self._response_summary(resp)
                for code, resp in op.responses.items()
            },
            "produces_ids": op.produces_ids,
            "consumes_ids": op.consumes_ids,
        }

    def _parameter_summary(self, p: ParameterInfo) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": p.name,
            "in": p.location,
            "required": p.required,
        }
        if p.schema_ is not None:
            data["schema"] = self._schema_summary(p.schema_)
        if p.example is not None:
            data["example"] = p.example
        return data

    def _request_body_summary(
        self, rb: RequestBodyInfo | None
    ) -> dict[str, Any] | None:
        if rb is None:
            return None
        return {
            "required": rb.required,
            "content": {
                media: self._schema_summary(schema)
                for media, schema in rb.content.items()
            },
        }

    def _response_summary(self, resp: ResponseInfo) -> dict[str, Any]:
        return {
            "description": resp.description,
            "content": {
                media: self._schema_summary(schema)
                for media, schema in resp.content.items()
            },
        }

    def _schema_summary(self, schema: SchemaInfo) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if schema.resolved_ref:
            data["$ref"] = schema.resolved_ref
        if schema.type is not None:
            data["type"] = schema.type
        if schema.format:
            data["format"] = schema.format
        if schema.required:
            data["required"] = schema.required
        if schema.enum:
            data["enum"] = schema.enum
        if schema.properties:
            data["properties"] = {
                name: self._schema_summary(prop)
                for name, prop in schema.properties.items()
            }
        if schema.items is not None:
            data["items"] = self._schema_summary(schema.items)
        for key in (
            "minimum",
            "maximum",
            "min_length",
            "max_length",
            "min_items",
            "max_items",
            "pattern",
        ):
            value = getattr(schema, key)
            if value is not None:
                data[key] = value
        if schema.example is not None:
            data["example"] = schema.example
        if schema.examples:
            data["examples"] = schema.examples
        return data
