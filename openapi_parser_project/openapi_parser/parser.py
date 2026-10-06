from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import OpenAPILoader
from .models import (
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


class OpenAPIParser:
    """
    Parser OpenAPI 3.x.

    На вход:
        - путь к JSON/YAML;
        - dict с уже загруженным OpenAPI документом.

    На выход:
        OpenAPIContract
    """

    def __init__(self) -> None:
        self.loader = OpenAPILoader()

    def parse(
        self,
        source: str | Path | dict[str, Any],
    ) -> OpenAPIContract:

        document = self.loader.load(source)

        self._validate_openapi_version(document)

        return OpenAPIContract(
            openapi_version=document["openapi"],
            title=self._get_title(document),
            api_version=self._get_api_version(document),
            description=document.get("info", {}).get("description"),
            servers=self._parse_servers(document),
            operations=self._parse_operations(document),
            schemas=self._parse_components(document),
            raw=document,
        )

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------

    def _validate_openapi_version(
        self,
        document: dict[str, Any],
    ) -> None:

        version = document.get("openapi")

        if not isinstance(version, str):
            raise ValueError(
                "Missing required 'openapi' field"
            )

        if not version.startswith("3."):
            raise ValueError(
                f"Only OpenAPI 3.x is supported. "
                f"Received: {version}"
            )

        if "info" not in document:
            raise ValueError(
                "OpenAPI document must contain 'info'"
            )

        if "paths" not in document:
            raise ValueError(
                "OpenAPI document must contain 'paths'"
            )

    # ---------------------------------------------------------
    # Info
    # ---------------------------------------------------------

    def _get_title(
        self,
        document: dict[str, Any],
    ) -> str:

        info = document.get("info", {})

        title = info.get("title")

        if not title:
            raise ValueError(
                "OpenAPI 'info.title' is required"
            )

        return str(title)

    def _get_api_version(
        self,
        document: dict[str, Any],
    ) -> str:

        info = document.get("info", {})

        version = info.get("version")

        if not version:
            raise ValueError(
                "OpenAPI 'info.version' is required"
            )

        return str(version)

    # ---------------------------------------------------------
    # Servers
    # ---------------------------------------------------------

    def _parse_servers(
        self,
        document: dict[str, Any],
    ) -> list[ServerInfo]:

        result: list[ServerInfo] = []

        for server in document.get("servers", []):
            if not isinstance(server, dict):
                continue

            result.append(
                ServerInfo(
                    url=server.get("url", ""),
                    description=server.get("description"),
                    variables=server.get("variables", {}),
                )
            )

        return result

    # ---------------------------------------------------------
    # Paths / operations
    # ---------------------------------------------------------

    def _parse_operations(
        self,
        document: dict[str, Any],
    ) -> list[OperationInfo]:

        operations: list[OperationInfo] = []

        paths = document.get("paths", {})

        if not isinstance(paths, dict):
            return operations

        for path, path_item in paths.items():

            if not isinstance(path_item, dict):
                continue

            path_parameters = self._parse_parameters(
                path_item.get("parameters", [])
            )

            for method, operation in path_item.items():

                method_lower = method.lower()

                if method_lower not in HTTP_METHODS:
                    continue

                if not isinstance(operation, dict):
                    continue

                operation_parameters = self._parse_parameters(
                    operation.get("parameters", [])
                )

                parameters = self._merge_parameters(
                    path_parameters,
                    operation_parameters,
                )

                request_body = self._parse_request_body(
                    operation.get("requestBody")
                )

                responses = self._parse_responses(
                    operation.get("responses", {})
                )

                operations.append(
                    OperationInfo(
                        path=path,
                        method=method.upper(),
                        operation_id=operation.get(
                            "operationId"
                        ),
                        summary=operation.get("summary"),
                        description=operation.get(
                            "description"
                        ),
                        tags=operation.get("tags", []),
                        parameters=parameters,
                        request_body=request_body,
                        responses=responses,
                        security=operation.get("security"),
                    )
                )

        return operations

    # ---------------------------------------------------------
    # Parameters
    # ---------------------------------------------------------

    def _parse_parameters(
        self,
        parameters: list[Any],
    ) -> list[ParameterInfo]:

        result: list[ParameterInfo] = []

        if not isinstance(parameters, list):
            return result

        for parameter in parameters:

            if not isinstance(parameter, dict):
                continue

            # $ref пока сохраняем как reference.
            if "$ref" in parameter:
                result.append(
                    ParameterInfo(
                        name=parameter["$ref"],
                        **{"in": "ref"},
                        required=False,
                    )
                )
                continue

            name = parameter.get("name")
            location = parameter.get("in")

            if not name or not location:
                continue

            schema = None

            if isinstance(parameter.get("schema"), dict):
                schema = self._parse_schema(
                    parameter["schema"]
                )

            content = self._parse_content(
                parameter.get("content", {})
            )

            result.append(
                ParameterInfo(
                    name=name,
                    **{"in": location},
                    required=bool(
                        parameter.get("required", False)
                    ),
                    description=parameter.get(
                        "description"
                    ),
                    schema=schema,
                    content=content,
                )
            )

        return result

    def _merge_parameters(
        self,
        path_parameters: list[ParameterInfo],
        operation_parameters: list[ParameterInfo],
    ) -> list[ParameterInfo]:

        merged: dict[tuple[str, str], ParameterInfo] = {}

        for parameter in path_parameters:
            key = (
                parameter.location,
                parameter.name,
            )
            merged[key] = parameter

        for parameter in operation_parameters:
            key = (
                parameter.location,
                parameter.name,
            )
            merged[key] = parameter

        return list(merged.values())

    # ---------------------------------------------------------
    # Request body
    # ---------------------------------------------------------

    def _parse_request_body(
        self,
        request_body: Any,
    ) -> RequestBodyInfo | None:

        if not isinstance(request_body, dict):
            return None

        if "$ref" in request_body:
            return RequestBodyInfo(
                description=request_body["$ref"]
            )

        return RequestBodyInfo(
            description=request_body.get(
                "description"
            ),
            required=bool(
                request_body.get("required", False)
            ),
            content=self._parse_content(
                request_body.get("content", {})
            ),
        )

    # ---------------------------------------------------------
    # Responses
    # ---------------------------------------------------------

    def _parse_responses(
        self,
        responses: Any,
    ) -> dict[str, ResponseInfo]:

        result: dict[str, ResponseInfo] = {}

        if not isinstance(responses, dict):
            return result

        for status_code, response in responses.items():

            if not isinstance(response, dict):
                continue

            if "$ref" in response:
                result[status_code] = ResponseInfo(
                    status_code=status_code,
                    description=response["$ref"],
                )
                continue

            result[status_code] = ResponseInfo(
                status_code=status_code,
                description=response.get(
                    "description",
                    "",
                ),
                content=self._parse_content(
                    response.get("content", {})
                ),
                headers=response.get(
                    "headers",
                    {},
                ),
            )

        return result

    # ---------------------------------------------------------
    # Content / media types
    # ---------------------------------------------------------

    def _parse_content(
        self,
        content: Any,
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
                    schema
                )

        return result

    # ---------------------------------------------------------
    # Components
    # ---------------------------------------------------------

    def _parse_components(
        self,
        document: dict[str, Any],
    ) -> dict[str, SchemaInfo]:

        components = document.get(
            "components",
            {},
        )

        if not isinstance(components, dict):
            return {}

        schemas = components.get(
            "schemas",
            {},
        )

        if not isinstance(schemas, dict):
            return {}

        return {
            name: self._parse_schema(schema)
            for name, schema in schemas.items()
            if isinstance(schema, dict)
        }

    # ---------------------------------------------------------
    # Schema
    # ---------------------------------------------------------

    def _parse_schema(
        self,
        schema: dict[str, Any],
    ) -> SchemaInfo:

        if "$ref" in schema:
            return SchemaInfo(
                ref=schema["$ref"]
            )

        schema_type = schema.get("type")

        # OpenAPI 3.1:
        # type can be ["string", "null"]
        nullable = bool(
            schema.get("nullable", False)
        )

        if isinstance(schema_type, list):
            nullable = nullable or (
                "null" in schema_type
            )

        properties: dict[str, SchemaInfo] = {}

        raw_properties = schema.get(
            "properties",
            {},
        )

        if isinstance(raw_properties, dict):
            for name, property_schema in (
                raw_properties.items()
            ):
                if isinstance(property_schema, dict):
                    properties[name] = self._parse_schema(
                        property_schema
                    )

        items = None

        if isinstance(schema.get("items"), dict):
            items = self._parse_schema(
                schema["items"]
            )

        all_of = self._parse_schema_list(
            schema.get("allOf", [])
        )

        one_of = self._parse_schema_list(
            schema.get("oneOf", [])
        )

        any_of = self._parse_schema_list(
            schema.get("anyOf", [])
        )

        additional_properties = (
            schema.get("additionalProperties")
        )

        if isinstance(
            additional_properties,
            dict,
        ):
            additional_properties = (
                self._parse_schema(
                    additional_properties
                )
            )

        return SchemaInfo(
            type=schema_type,
            format=schema.get("format"),
            title=schema.get("title"),
            description=schema.get(
                "description"
            ),
            nullable=nullable,
            required=schema.get(
                "required",
                [],
            ),
            properties=properties,
            items=items,
            enum=schema.get(
                "enum",
                [],
            ),
            default=schema.get(
                "default"
            ),
            example=schema.get(
                "example"
            ),
            minimum=schema.get(
                "minimum"
            ),
            maximum=schema.get(
                "maximum"
            ),
            exclusive_minimum=schema.get(
                "exclusiveMinimum"
            ),
            exclusive_maximum=schema.get(
                "exclusiveMaximum"
            ),
            min_length=schema.get(
                "minLength"
            ),
            max_length=schema.get(
                "maxLength"
            ),
            min_items=schema.get(
                "minItems"
            ),
            max_items=schema.get(
                "maxItems"
            ),
            unique_items=schema.get(
                "uniqueItems"
            ),
            pattern=schema.get(
                "pattern"
            ),
            all_of=all_of,
            one_of=one_of,
            any_of=any_of,
            additional_properties=additional_properties,
        )

    def _parse_schema_list(
        self,
        schemas: Any,
    ) -> list[SchemaInfo]:

        if not isinstance(schemas, list):
            return []

        return [
            self._parse_schema(schema)
            for schema in schemas
            if isinstance(schema, dict)
        ]