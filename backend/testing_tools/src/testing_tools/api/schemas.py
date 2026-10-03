"""Контракты HTTP API модуля testing_tools.

Модуль принимает от агента задания на прогон black-box инструментов
(Schemathesis, Microcks, мутационное тестирование на уровне контракта)
против развёрнутого сервиса и отдаёт нормализованные результаты.

Прогон асинхронный:
    POST /api/v1/runs                        -> 202 RunCreated
    GET  /api/v1/runs/{run_id}               -> 200 RunStatus      (поллинг)
    GET  /api/v1/runs/{run_id}/result        -> 200 RunResult      (только в терминальном статусе)
    POST /api/v1/runs/{run_id}/cancel        -> 202 RunStatus
    GET  /api/v1/runs/{run_id}/artifacts/{n} -> 200 файл (сырые отчёты инструментов)
    GET  /api/v1/tools                       -> 200 list[ToolInfo]
    GET  /health                             -> 200 HealthResponse

Любая ошибка (4xx/5xx) возвращается как ProblemDetail (application/problem+json).

Подробное описание с примерами: backend/testing_tools/docs/API.md
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

API_VERSION = "v1"


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- #
# Общие типы
# --------------------------------------------------------------------------- #


class ToolName(StrEnum):
    SCHEMATHESIS = "schemathesis"
    MICROCKS = "microcks"
    MUTATION = "mutation"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    TRACE = "TRACE"


class ContractFormat(StrEnum):
    JSON = "json"
    YAML = "yaml"


class UrlContractSource(Schema):
    """Контракт скачивается модулем по URL."""

    kind: Literal["url"] = "url"
    url: AnyHttpUrl
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Заголовки для скачивания контракта (например, Authorization).",
    )


class InlineContractSource(Schema):
    """Контракт передаётся прямо в теле запроса."""

    kind: Literal["inline"] = "inline"
    format: ContractFormat
    content: str = Field(min_length=1, description="Текст OpenAPI 3.x документа.")


ContractSource = Annotated[
    UrlContractSource | InlineContractSource, Field(discriminator="kind")
]


class Target(Schema):
    """Развёрнутый тестируемый сервис."""

    base_url: AnyHttpUrl = Field(
        description="Базовый URL сервиса, к которому инструменты шлют запросы. "
        "Должен быть достижим из сети модуля testing_tools.",
        examples=["http://petstore:8080/api"],
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Заголовки, добавляемые к каждому запросу к сервису (auth и т.п.). "
        "В логах и результатах значения маскируются.",
    )
    request_timeout_s: float = Field(default=10.0, gt=0, le=300)
    tls_verify: bool = True


class OperationRef(Schema):
    """Ссылка на операцию контракта."""

    method: HttpMethod
    path: str = Field(examples=["/pets/{petId}"])
    operation_id: str | None = None


class OperationFilter(Schema):
    """Фильтр операций контракта. Пустой фильтр = все операции.

    Операция попадает в прогон, если подходит под include (или include пуст)
    и не подходит под exclude. Внутри include/exclude поля объединяются по AND,
    значения внутри одного поля — по OR.
    """

    include: "OperationMatcher | None" = None
    exclude: "OperationMatcher | None" = None


class OperationMatcher(Schema):
    path_regex: list[str] = Field(default_factory=list)
    methods: list[HttpMethod] = Field(default_factory=list)
    operation_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


OperationFilter.model_rebuild()


# --------------------------------------------------------------------------- #
# Конфиги инструментов (вход)
# --------------------------------------------------------------------------- #


class SchemathesisCheck(StrEnum):
    NOT_A_SERVER_ERROR = "not_a_server_error"
    STATUS_CODE_CONFORMANCE = "status_code_conformance"
    CONTENT_TYPE_CONFORMANCE = "content_type_conformance"
    RESPONSE_HEADERS_CONFORMANCE = "response_headers_conformance"
    RESPONSE_SCHEMA_CONFORMANCE = "response_schema_conformance"
    NEGATIVE_DATA_REJECTION = "negative_data_rejection"
    POSITIVE_DATA_ACCEPTANCE = "positive_data_acceptance"
    MISSING_REQUIRED_HEADER = "missing_required_header"
    UNSUPPORTED_METHOD = "unsupported_method"
    IGNORED_AUTH = "ignored_auth"
    USE_AFTER_FREE = "use_after_free"
    ENSURE_RESOURCE_AVAILABILITY = "ensure_resource_availability"
    MAX_RESPONSE_TIME = "max_response_time"


class SchemathesisPhase(StrEnum):
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"
    STATEFUL = "stateful"


class GenerationMode(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    ALL = "all"


class SchemathesisConfig(Schema):
    tool: Literal[ToolName.SCHEMATHESIS] = ToolName.SCHEMATHESIS
    checks: list[SchemathesisCheck] | None = Field(
        default=None, description="Набор проверок. null = все проверки."
    )
    exclude_checks: list[SchemathesisCheck] = Field(default_factory=list)
    phases: list[SchemathesisPhase] = Field(
        default_factory=lambda: list(SchemathesisPhase), min_length=1
    )
    mode: GenerationMode = GenerationMode.ALL
    max_examples: int = Field(default=100, ge=1, le=10_000)
    seed: int | None = Field(default=None, description="Для воспроизводимости прогона.")
    workers: int = Field(default=1, ge=1, le=64)
    max_response_time_ms: int | None = Field(
        default=None, gt=0, description="Порог для проверки max_response_time."
    )
    max_failures: int | None = Field(
        default=None, ge=1, description="Остановить прогон после N падений."
    )
    operations: OperationFilter | None = None


class MicrocksRunner(StrEnum):
    OPEN_API_SCHEMA = "OPEN_API_SCHEMA"
    HTTP = "HTTP"


class MicrocksConfig(Schema):
    """Contract-тест в Microcks: сервис импортируется из контракта, затем
    Microcks прогоняет примеры (examples) из контракта против target."""

    tool: Literal[ToolName.MICROCKS] = ToolName.MICROCKS
    runner: MicrocksRunner = MicrocksRunner.OPEN_API_SCHEMA
    timeout_ms: int = Field(default=10_000, ge=100, le=600_000)
    service_name: str | None = Field(
        default=None, description="По умолчанию info.title из контракта."
    )
    service_version: str | None = Field(
        default=None, description="По умолчанию info.version из контракта."
    )
    filtered_operations: list[str] | None = Field(
        default=None,
        description='Имена операций в нотации Microcks ("GET /pets"). null = все.',
        examples=[["GET /pets", "POST /pets"]],
    )
    operations_headers: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description='Заголовки по операциям. Ключ "globals" — для всех операций.',
        examples=[{"globals": {"X-Tenant": "demo"}, "GET /pets": {"X-Debug": "1"}}],
    )
    secret_name: str | None = Field(
        default=None, description="Имя секрета, заранее заведённого в Microcks."
    )
    cleanup: bool = Field(
        default=True, description="Удалить импортированный сервис из Microcks после прогона."
    )


class MutationOperator(StrEnum):
    """Операторы мутации данных на уровне контракта (ТЗ п.2.2).

    Мутации применяются к ответам сервиса через мутирующий прокси,
    который встаёт между тестами и target.
    """

    CHANGE_FIELD_TYPE = "change_field_type"
    REMOVE_REQUIRED_FIELD = "remove_required_field"
    REPLACE_STATUS_CODE = "replace_status_code"


class InlineTestSuite(Schema):
    kind: Literal["inline"] = "inline"
    files: dict[str, str] = Field(
        min_length=1,
        description="Относительный путь -> содержимое файла (тесты, conftest.py и т.п.).",
        examples=[{"tests/test_pets.py": "import os, httpx\n..."}],
    )


class GitTestSuite(Schema):
    kind: Literal["git"] = "git"
    repo_url: str
    ref: str = Field(default="HEAD", description="Ветка, тег или sha.")
    subdir: str = Field(default=".", description="Каталог с тестами внутри репозитория.")


TestSuiteSource = Annotated[InlineTestSuite | GitTestSuite, Field(discriminator="kind")]


class MutationConfig(Schema):
    """Мутационное тестирование сгенерированных агентом pytest-тестов.

    1. Baseline: тесты запускаются против немутированного сервиса (через прокси
       без мутаций) — отсюда Runnability.
    2. Для каждого мутанта тесты перезапускаются; если хотя бы один тест,
       прошедший на baseline, упал — мутант убит. Отсюда Mutation Score.
    """

    tool: Literal[ToolName.MUTATION] = ToolName.MUTATION
    tests: TestSuiteSource
    requirements: list[str] = Field(
        default_factory=list,
        description="Доп. pip-зависимости тестов (pytest и httpx ставятся всегда).",
        examples=[["requests==2.32.3"]],
    )
    base_url_env: str = Field(
        default="ARQA_BASE_URL",
        description="Имя env-переменной, из которой тесты берут URL сервиса. "
        "Модуль подставит туда адрес мутирующего прокси.",
    )
    pytest_args: list[str] = Field(default_factory=list, examples=[["-k", "not slow"]])
    operators: list[MutationOperator] = Field(
        default_factory=lambda: list(MutationOperator), min_length=1
    )
    max_mutants: int | None = Field(
        default=None, ge=1, description="Ограничение числа мутантов (случайная выборка по seed)."
    )
    per_mutant_timeout_s: float = Field(default=120.0, gt=0, le=3600)
    seed: int | None = None
    operations: OperationFilter | None = None


ToolConfig = Annotated[
    SchemathesisConfig | MicrocksConfig | MutationConfig, Field(discriminator="tool")
]


# --------------------------------------------------------------------------- #
# POST /api/v1/runs
# --------------------------------------------------------------------------- #


class RunCreateRequest(Schema):
    contract: ContractSource
    target: Target
    tools: list[ToolConfig] = Field(
        min_length=1, description="Инструменты прогона; каждый — не более одного раза."
    )
    timeout_s: int = Field(
        default=1800, ge=10, le=86_400, description="Общий таймаут прогона."
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Произвольные метки агента (session_id, service, iteration...). "
        "Возвращаются как есть.",
    )

    @model_validator(mode="after")
    def _unique_tools(self) -> "RunCreateRequest":
        names = [t.tool for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("each tool may appear in `tools` at most once")
        return self


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # все инструменты отработали (находки — не ошибка)
    FAILED = "failed"  # прогон не смог выполниться (контракт/target недоступны и т.п.)
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_RUN_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.TIMED_OUT}
)


class ToolState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"  # отработал, находок нет
    FAILED = "failed"  # отработал, есть находки / выжившие мутанты / падения baseline
    ERROR = "error"  # сам инструмент не отработал
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ErrorInfo(Schema):
    code: "ErrorCode"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RunLinks(Schema):
    self: str = Field(examples=["/api/v1/runs/7d1c..."])
    result: str
    cancel: str


class RunCreated(Schema):
    run_id: UUID
    state: RunState
    created_at: datetime
    links: RunLinks


# --------------------------------------------------------------------------- #
# GET /api/v1/runs/{run_id}
# --------------------------------------------------------------------------- #


class ToolProgress(Schema):
    tool: ToolName
    state: ToolState
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float | None = Field(
        default=None, ge=0, le=1, description="Оценка прогресса, если инструмент её даёт."
    )
    error: ErrorInfo | None = None


class RunStatus(Schema):
    run_id: UUID
    state: RunState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tools: list[ToolProgress]
    labels: dict[str, str]
    error: ErrorInfo | None = Field(
        default=None, description="Заполнен при state=failed/timed_out."
    )
    links: RunLinks


# --------------------------------------------------------------------------- #
# GET /api/v1/runs/{run_id}/result
# --------------------------------------------------------------------------- #


class ContractInfo(Schema):
    title: str
    version: str
    openapi_version: str = Field(examples=["3.0.3", "3.1.0"])
    operations_total: int
    sha256: str = Field(description="Хэш контракта, по которому шёл прогон.")


class ArtifactRef(Schema):
    """Сырые отчёты инструментов (JUnit XML, NDJSON, HAR, логи pytest...)."""

    name: str = Field(examples=["schemathesis-junit.xml"])
    media_type: str = Field(examples=["application/xml"])
    size_bytes: int
    url: str = Field(examples=["/api/v1/runs/7d1c.../artifacts/schemathesis-junit.xml"])


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class HttpRequestSnapshot(Schema):
    method: HttpMethod
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, description="Обрезается до 64 KiB.")


class HttpResponseSnapshot(Schema):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, description="Обрезается до 64 KiB.")
    elapsed_ms: float | None = None


class Finding(Schema):
    """Нормализованная находка инструмента — единый формат для агента."""

    id: str = Field(description="Стабильный в пределах прогона id (для дедупликации).")
    tool: ToolName
    check: str = Field(
        description="Имя проверки в терминах инструмента "
        "(например, response_schema_conformance).",
    )
    severity: Severity
    operation: OperationRef
    title: str
    message: str
    request: HttpRequestSnapshot | None = None
    response: HttpResponseSnapshot | None = None
    reproduce: str | None = Field(default=None, description="curl-команда для воспроизведения.")


class OperationCoverage(Schema):
    operation: OperationRef
    tested: bool
    documented_status_codes: list[str] = Field(examples=[["200", "404", "default"]])
    observed_status_codes: list[int] = Field(examples=[[200, 404, 422]])
    requests: int


class ContractCoverage(Schema):
    """Покрытие контракта (метрика Contract Coverage)."""

    operations_total: int
    operations_tested: int
    operations_ratio: float = Field(ge=0, le=1)
    status_codes_documented: int
    status_codes_observed: int = Field(description="Из задокументированных.")
    status_codes_ratio: float = Field(ge=0, le=1)
    undocumented_status_codes_observed: int
    by_operation: list[OperationCoverage]


class ToolResultBase(Schema):
    state: ToolState
    tool_version: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    error: ErrorInfo | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)


# --- Schemathesis ----------------------------------------------------------- #


class SchemathesisSummary(Schema):
    operations_selected: int
    operations_tested: int
    test_cases: int
    requests: int
    failures: int = Field(description="Уникальные падения (после дедупликации).")
    failures_by_check: dict[str, int]
    errors: int = Field(description="Ошибки генерации/сети, не связанные с сервисом.")


class SchemathesisResult(ToolResultBase):
    tool: Literal[ToolName.SCHEMATHESIS] = ToolName.SCHEMATHESIS
    seed: int | None = Field(default=None, description="Фактический seed прогона.")
    summary: SchemathesisSummary | None = None
    coverage: ContractCoverage | None = None
    findings: list[Finding] = Field(default_factory=list)


# --- Microcks --------------------------------------------------------------- #


class MicrocksStepResult(Schema):
    request_name: str = Field(description="Имя примера из контракта.")
    success: bool
    elapsed_ms: float | None = None
    message: str | None = None


class MicrocksOperationResult(Schema):
    operation: OperationRef
    microcks_operation: str = Field(examples=["GET /pets/{petId}"])
    success: bool
    elapsed_ms: float | None = None
    steps: list[MicrocksStepResult]


class MicrocksSummary(Schema):
    operations_total: int
    operations_passed: int
    operations_failed: int
    steps_total: int
    steps_passed: int


class MicrocksResult(ToolResultBase):
    tool: Literal[ToolName.MICROCKS] = ToolName.MICROCKS
    test_id: str | None = Field(default=None, description="TestResult id в Microcks.")
    test_url: str | None = Field(default=None, description="Ссылка на результат в UI Microcks.")
    service_ref: str | None = Field(default=None, examples=["Petstore API:1.0.0"])
    summary: MicrocksSummary | None = None
    operations: list[MicrocksOperationResult] = Field(default_factory=list)
    coverage: ContractCoverage | None = None
    findings: list[Finding] = Field(default_factory=list)


# --- Mutation --------------------------------------------------------------- #


class TestOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class TestCaseResult(Schema):
    node_id: str = Field(examples=["tests/test_pets.py::test_get_pet_not_found"])
    outcome: TestOutcome
    duration_s: float
    message: str | None = Field(default=None, description="Короткий текст ошибки/assert.")


class BaselineResult(Schema):
    """Прогон тестов против немутированного сервиса (метрика Runnability)."""

    tests_total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    collection_errors: list[str] = Field(
        default_factory=list, description="Файлы, которые pytest не смог собрать."
    )
    runnability: float = Field(ge=0, le=1, description="passed / tests_total.")
    duration_s: float
    tests: list[TestCaseResult]


class MutantState(StrEnum):
    KILLED = "killed"
    SURVIVED = "survived"
    TIMEOUT = "timeout"  # считается убитым
    NO_COVERAGE = "no_coverage"  # ни один тест не вызвал мутированную операцию
    ERROR = "error"


class Mutant(Schema):
    id: str
    operator: MutationOperator
    operation: OperationRef
    status_code: str = Field(description="Ответ контракта, к которому применена мутация.")
    location: str | None = Field(
        default=None,
        description="JSON Pointer мутированного поля в теле ответа.",
        examples=["/items/0/id"],
    )
    description: str = Field(examples=["integer -> string in /id"])
    state: MutantState
    killed_by: list[str] = Field(default_factory=list, description="node_id упавших тестов.")
    duration_s: float | None = None


class MutationSummary(Schema):
    mutants_total: int
    killed: int
    survived: int
    timeout: int
    no_coverage: int
    errors: int
    mutation_score: float = Field(
        ge=0, le=1, description="(killed + timeout) / (total - errors - no_coverage)."
    )
    by_operator: dict[MutationOperator, float] = Field(
        description="mutation score по каждому оператору."
    )


class MutationResult(ToolResultBase):
    tool: Literal[ToolName.MUTATION] = ToolName.MUTATION
    seed: int | None = None
    baseline: BaselineResult | None = None
    summary: MutationSummary | None = None
    mutants: list[Mutant] = Field(default_factory=list)


ToolResult = Annotated[
    SchemathesisResult | MicrocksResult | MutationResult, Field(discriminator="tool")
]


class RunResult(Schema):
    run_id: UUID
    state: RunState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    labels: dict[str, str]
    contract: ContractInfo | None = Field(
        default=None, description="null, если контракт не удалось загрузить/разобрать."
    )
    target_base_url: str
    tools: list[ToolResult]
    error: ErrorInfo | None = None


# --------------------------------------------------------------------------- #
# GET /api/v1/tools, GET /health
# --------------------------------------------------------------------------- #


class ToolInfo(Schema):
    tool: ToolName
    available: bool
    version: str | None = None
    description: str
    config_schema: dict[str, Any] = Field(description="JSON Schema конфига инструмента.")


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"  # часть инструментов недоступна (например, Microcks лежит)


class HealthResponse(Schema):
    status: HealthState
    version: str
    tools: dict[ToolName, bool]


# --------------------------------------------------------------------------- #
# Ошибки (RFC 9457, application/problem+json)
# --------------------------------------------------------------------------- #


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"  # 422: тело запроса не прошло схему
    CONTRACT_UNREACHABLE = "contract_unreachable"  # 422: не скачался по url
    CONTRACT_INVALID = "contract_invalid"  # 422: не OpenAPI 3.x / битый документ
    TARGET_UNREACHABLE = "target_unreachable"  # 422 на POST или error в прогоне
    TOOL_UNAVAILABLE = "tool_unavailable"  # 503: инструмент не поднят
    TOOL_FAILED = "tool_failed"  # только внутри ErrorInfo инструмента
    TEST_SUITE_INVALID = "test_suite_invalid"  # 422: тесты не скачались / не собрались
    RUN_NOT_FOUND = "run_not_found"  # 404
    RUN_NOT_FINISHED = "run_not_finished"  # 409: result запрошен раньше времени
    RUN_ALREADY_FINISHED = "run_already_finished"  # 409: cancel завершённого
    ARTIFACT_NOT_FOUND = "artifact_not_found"  # 404
    TOO_MANY_RUNS = "too_many_runs"  # 429: очередь заполнена
    TIMEOUT = "timeout"  # только внутри ErrorInfo
    INTERNAL_ERROR = "internal_error"  # 500


ErrorInfo.model_rebuild()


class FieldError(Schema):
    loc: list[str | int]
    msg: str
    type: str


class ProblemDetail(Schema):
    type: str = Field(default="about:blank")
    title: str
    status: int
    detail: str | None = None
    code: ErrorCode
    errors: list[FieldError] = Field(
        default_factory=list, description="Заполняется для validation_error."
    )


if __name__ == "__main__":
    # Дамп JSON Schema всех контрактов: python -m testing_tools.api.schemas > schemas.json
    import json

    from pydantic.json_schema import models_json_schema

    _, defs = models_json_schema(
        [
            (m, "validation")
            for m in (
                RunCreateRequest,
                RunCreated,
                RunStatus,
                RunResult,
                ToolInfo,
                HealthResponse,
                ProblemDetail,
            )
        ],
        title="testing_tools API v1",
    )
    print(json.dumps(defs, ensure_ascii=False, indent=2))
