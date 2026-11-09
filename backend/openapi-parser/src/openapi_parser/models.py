from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractStats(BaseModel):
    """Краткая статистика по контракту (ТЗ п.1.3)."""

    source: str | None = None
    title: str = ""
    api_version: str = ""
    openapi_version: str = ""
    spec_type: str = "OpenAPI 3.x"
    operations_count: int = 0
    paths_count: int = 0
    schemas_count: int = 0
    servers_count: int = 0

    def as_text(self) -> str:
        return (
            f"Источник: {self.source or '-'}\n"
            f"Название: {self.title}\n"
            f"Версия API: {self.api_version}\n"
            f"Спецификация: {self.spec_type} ({self.openapi_version})\n"
            f"Операций: {self.operations_count}\n"
            f"Путей: {self.paths_count}\n"
            f"Схем: {self.schemas_count}\n"
            f"Серверов: {self.servers_count}"
        )


class SchemaInfo(BaseModel):
    """Нормализованное представление OpenAPI Schema Object."""

    model_config = ConfigDict(extra="allow")

    ref: str | None = None
    resolved_ref: str | None = None

    type: str | list[str] | None = None
    format: str | None = None

    title: str | None = None
    description: str | None = None

    nullable: bool = False

    required: list[str] = Field(default_factory=list)

    properties: dict[str, SchemaInfo] = Field(default_factory=dict)

    items: SchemaInfo | None = None

    enum: list[Any] = Field(default_factory=list)

    default: Any = None
    example: Any = None
    examples: list[Any] = Field(default_factory=list)

    minimum: int | float | None = None
    maximum: int | float | None = None

    exclusive_minimum: bool | int | float | None = None
    exclusive_maximum: bool | int | float | None = None

    min_length: int | None = None
    max_length: int | None = None

    min_items: int | None = None
    max_items: int | None = None

    unique_items: bool | None = None

    pattern: str | None = None

    all_of: list[SchemaInfo] = Field(default_factory=list)
    one_of: list[SchemaInfo] = Field(default_factory=list)
    any_of: list[SchemaInfo] = Field(default_factory=list)

    additional_properties: bool | SchemaInfo | None = None


SchemaInfo.model_rebuild()


class ParameterInfo(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    location: str = Field(alias="in")

    required: bool = False

    description: str | None = None

    schema_: SchemaInfo | None = Field(default=None, alias="schema")

    content: dict[str, SchemaInfo] = Field(default_factory=dict)

    example: Any = None
    examples: list[Any] = Field(default_factory=list)


class RequestBodyInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str | None = None
    required: bool = False

    content: dict[str, SchemaInfo] = Field(default_factory=dict)

    example: Any = None


class ResponseInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    status_code: str

    description: str = ""

    content: dict[str, SchemaInfo] = Field(default_factory=dict)

    headers: dict[str, Any] = Field(default_factory=dict)


class OperationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    method: str

    operation_id: str | None = None

    summary: str | None = None
    description: str | None = None

    tags: list[str] = Field(default_factory=list)

    parameters: list[ParameterInfo] = Field(default_factory=list)

    request_body: RequestBodyInfo | None = None

    responses: dict[str, ResponseInfo] = Field(default_factory=dict)

    security: list[dict[str, list[str]]] | None = None

    # ТЗ п.2.4: связи между операциями
    produces_ids: list[str] = Field(default_factory=list)
    consumes_ids: list[str] = Field(default_factory=list)

    def summary_line(self) -> str:
        return f"{self.method} {self.path} ({self.operation_id or '-'})"


class ServerInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    description: str | None = None

    variables: dict[str, Any] = Field(default_factory=dict)


class OpenAPIContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    openapi_version: str

    title: str
    api_version: str

    description: str | None = None

    servers: list[ServerInfo] = Field(default_factory=list)

    operations: list[OperationInfo] = Field(default_factory=list)

    schemas: dict[str, SchemaInfo] = Field(default_factory=dict)

    stats: ContractStats = Field(default_factory=ContractStats)

    summary: dict[str, Any] = Field(default_factory=dict)

    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def version(self) -> str:
        return self.openapi_version