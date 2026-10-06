# testing_tools — HTTP API v1

Сервис-обёртка над black-box инструментами тестирования API (ТЗ п.2.2: «Контрактное
тестирование методом чёрного ящика обеспечивается интеграцией с существующим
инструментом в виде отдельного модуля/микросервиса»).

Агент присылает: **контракт** (OpenAPI 3.x) + **адрес развёрнутого сервиса** +
**конфиги инструментов**. Модуль запускает инструменты и отдаёт нормализованный
результат: находки, покрытие контракта, мутационный скор.

| Инструмент     | Что делает                                                              | Метрики ТЗ, которые даёт               |
|----------------|-------------------------------------------------------------------------|----------------------------------------|
| `schemathesis` | Property-based фаззинг по контракту (baseline-инструмент)               | Contract Coverage, Fault Detection     |
| `microcks`     | Contract-тесты по `examples` из контракта (baseline-инструмент)         | Contract Coverage, Fault Detection     |
| `mutation`     | Мутационное тестирование **тестов агента** через мутирующий прокси       | Runnability, Mutation Score            |

Источник истины по схемам — Pydantic-модели в
[`src/testing_tools/api/schemas.py`](../src/testing_tools/api/schemas.py).
JSON Schema всех моделей:

```bash
uv run python -m testing_tools.api.schemas > schemas.json
```

---

## Общие правила

- Базовый путь: `/api/v1`. Формат: `application/json`, UTF-8.
- Время — ISO 8601 с таймзоной (UTC), id прогона — UUID.
- Неизвестные поля в запросе → `422 validation_error` (`extra="forbid"`).
- Все ошибки — `application/problem+json` в формате `ProblemDetail` (см. ниже).
- Прогоны **асинхронные**: `POST` сразу возвращает `202`, дальше агент поллит статус.
- Значения заголовков из `target.headers`, `contract.headers`,
  `operations_headers` маскируются в логах, артефактах и `Finding.request.headers`.
- Состояние прогонов и артефакты хранятся минимум 24 часа.

## Эндпоинты

| Метод  | Путь                                       | Ответ                          |
|--------|--------------------------------------------|--------------------------------|
| GET    | `/health`                                  | `200 HealthResponse`           |
| GET    | `/api/v1/tools`                            | `200 list[ToolInfo]`           |
| POST   | `/api/v1/runs`                             | `202 RunCreated`               |
| GET    | `/api/v1/runs/{run_id}`                    | `200 RunStatus`                |
| GET    | `/api/v1/runs/{run_id}/result`             | `200 RunResult`                |
| POST   | `/api/v1/runs/{run_id}/cancel`             | `202 RunStatus`                |
| GET    | `/api/v1/runs/{run_id}/artifacts/{name}`   | `200` файл (`media_type` из `ArtifactRef`) |

### Жизненный цикл

```
POST /runs ──► queued ──► running ──┬─► completed   (все инструменты отработали; находки ≠ ошибка)
                  │           │      ├─► failed      (контракт/target недоступны, внутренняя ошибка)
                  │           │      └─► timed_out   (превышен timeout_s)
                  └───────────┴────────► cancelled   (POST /cancel)
```

Статусы инструмента внутри прогона (`ToolState`):

| state       | Значение                                                                   |
|-------------|----------------------------------------------------------------------------|
| `pending`   | ждёт очереди                                                               |
| `running`   | выполняется                                                                |
| `passed`    | отработал, находок нет / все мутанты убиты и baseline зелёный              |
| `failed`    | отработал, есть находки / выжившие мутанты / упавшие на baseline тесты     |
| `error`     | сам инструмент не смог отработать (`error` заполнен)                        |
| `skipped`   | не запускался (например, run отменён до старта инструмента)                |
| `cancelled` | прерван отменой                                                            |

Инструменты одного прогона независимы: `error` одного не роняет остальные,
run при этом всё равно `completed`.

---

## POST /api/v1/runs

Синхронно (до ответа `202`) сервис обязан:

1. Провалидировать тело по схеме → `422 validation_error`.
2. Загрузить контракт (`url`) → `422 contract_unreachable`.
3. Разобрать контракт как OpenAPI 3.x → `422 contract_invalid`.
4. Проверить, что запрошенные инструменты доступны → `503 tool_unavailable`.
5. Для `mutation` с `kind=inline` — что `files` не пустой и пути относительные,
   без `..` → `422 test_suite_invalid`.

Доступность `target.base_url` проверяется уже в прогоне (сервис может подниматься):
`GET base_url`, до 3 попыток с паузой 1 с, годится любой HTTP-ответ. Если соединиться
не удалось — прогон `failed` с `error.code=target_unreachable`, инструменты `skipped`.
Если очередь заполнена → `429 too_many_runs` с заголовком `Retry-After`.

Опциональный заголовок `Idempotency-Key`: повторный `POST` с тем же ключом в течение
24 ч возвращает тот же `RunCreated`, новый прогон не создаётся.

### Тело запроса — `RunCreateRequest`

| Поле        | Тип                         | Обяз. | По умолчанию | Описание                                  |
|-------------|-----------------------------|-------|--------------|-------------------------------------------|
| `contract`  | `ContractSource`            | да    |              | Откуда взять OpenAPI                      |
| `target`    | `Target`                    | да    |              | Развёрнутый сервис                        |
| `tools`     | `list[ToolConfig]`, ≥1      | да    |              | Каждый инструмент — не более одного раза  |
| `timeout_s` | int, 10..86400              | нет   | `1800`       | Общий таймаут прогона                     |
| `labels`    | `dict[str, str]`            | нет   | `{}`         | Метки агента, возвращаются как есть       |

**`ContractSource`** (дискриминатор `kind`):

| kind     | Поля                                                     |
|----------|----------------------------------------------------------|
| `url`    | `url` (http/https), `headers?: dict[str,str]`            |
| `inline` | `format: "json" \| "yaml"`, `content: str`               |

**`Target`**:

| Поле                | Тип              | По умолчанию | Описание                                        |
|---------------------|------------------|--------------|-------------------------------------------------|
| `base_url`          | URL              |              | Должен быть достижим **из сети testing_tools**  |
| `headers`           | `dict[str,str]`  | `{}`         | Добавляются ко всем запросам к сервису          |
| `request_timeout_s` | float, (0, 300]  | `10.0`       | Таймаут одного HTTP-запроса                     |
| `tls_verify`        | bool             | `true`       |                                                 |

**`OperationFilter`** (используется в `schemathesis` и `mutation`):

```json
{
  "include": {"path_regex": ["^/pets"], "methods": ["GET", "POST"], "operation_ids": [], "tags": []},
  "exclude": {"methods": ["DELETE"]}
}
```

Операция берётся, если подходит под `include` (или он пуст) и не подходит под `exclude`.
Внутри matcher поля объединяются по AND, значения одного поля — по OR.

### `ToolConfig` (дискриминатор `tool`)

#### `schemathesis`

| Поле                   | Тип                        | По умолчанию     | Маппинг на Schemathesis CLI        |
|------------------------|----------------------------|------------------|------------------------------------|
| `checks`               | `list[SchemathesisCheck] \| null` | `null` (все) | `--checks`                        |
| `exclude_checks`       | `list[SchemathesisCheck]`  | `[]`             | `--exclude-checks`                 |
| `phases`               | `list[SchemathesisPhase]`  | все 4            | `--phases`                         |
| `mode`                 | `positive\|negative\|all`  | `all`            | `--mode`                           |
| `max_examples`         | int, 1..10000              | `100`            | `--max-examples`                   |
| `seed`                 | int \| null                | `null`           | `--seed`                           |
| `workers`              | int, 1..64                 | `1`              | `--workers`                        |
| `max_response_time_ms` | int \| null                | `null`           | `--max-response-time`              |
| `max_failures`         | int \| null                | `null`           | `--max-failures`                   |
| `operations`           | `OperationFilter \| null`  | `null`           | `--include-*` / `--exclude-*`      |

`SchemathesisCheck`: `not_a_server_error`, `status_code_conformance`,
`content_type_conformance`, `response_headers_conformance`,
`response_schema_conformance`, `negative_data_rejection`, `positive_data_acceptance`,
`missing_required_header`, `unsupported_method`, `ignored_auth`, `use_after_free`,
`ensure_resource_availability`, `max_response_time`.

`SchemathesisPhase`: `examples`, `coverage`, `fuzzing`, `stateful`.

`operations` применяется до запуска: Schemathesis получает копию контракта только с
выбранными операциями. Проверка `max_response_time` включается флагом
`--max-response-time` и работает, только если задан `max_response_time_ms`.
При `checks=null` запускаются все проверки Schemathesis, включая те, которых нет в
`SchemathesisCheck` (например, `allow_header_conformance`): `Finding.check` — строка.

`target.base_url` → `--url`, `target.headers` → `--header`,
`target.request_timeout_s` → `--request-timeout`, `target.tls_verify` → `--tls-verify`.
Сырые отчёты (`--report junit,ndjson,har`) сохранять как артефакты.

#### `microcks`

Модуль импортирует контракт в Microcks как artifact, создаёт Test
(`POST /api/tests`) против `target.base_url`, ждёт завершения, забирает результат.
Microcks тестирует только операции, для которых в контракте есть `examples`.

| Поле                  | Тип                             | По умолчанию        | Маппинг на Microcks Test   |
|-----------------------|---------------------------------|---------------------|----------------------------|
| `runner`              | `OPEN_API_SCHEMA \| HTTP`       | `OPEN_API_SCHEMA`   | `runnerType`               |
| `timeout_ms`          | int, 100..600000                | `10000`             | `timeout`                  |
| `service_name`        | str \| null                     | `info.title`        | `serviceId` (имя)          |
| `service_version`     | str \| null                     | `info.version`      | `serviceId` (версия)       |
| `filtered_operations` | `list[str] \| null`             | `null` (все)        | `filteredOperations`       |
| `operations_headers`  | `dict[str, dict[str,str]]`      | `{}`                | `operationsHeaders`        |
| `secret_name`         | str \| null                     | `null`              | `secretName`               |
| `cleanup`             | bool                            | `true`              | удалить сервис после теста |

`target.headers` мержатся в `operations_headers["globals"]` (явный `globals` побеждает).
Microcks сам ходит в `target.base_url`, поэтому адрес должен быть достижим из сети
Microcks. В находках Microcks `request.url` — шаблон пути (`/pets/{petId}`): Microcks
не отдаёт итоговый URL запроса.

#### `mutation`

Оценка качества **тестов, сгенерированных агентом** (ТЗ: операторы мутации на уровне
контракта — смена типа, удаление обязательного поля, подмена статус-кода).

Алгоритм:

1. Тесты из `tests` раскладываются в изолированное окружение
   (`pytest`, `httpx` + `requirements`), сеть — только до прокси.
2. Поднимается мутирующий HTTP-прокси перед `target.base_url`; его адрес передаётся
   тестам в env-переменной `base_url_env`.
3. **Baseline**: прогон без мутаций → `BaselineResult` (Runnability).
4. По контракту генерируются мутанты: для каждой (операция × документированный
   ответ × оператор × поле). Если задан `max_mutants` — случайная выборка по `seed`.
5. Для каждого мутанта прокси включает одну мутацию ответов, тесты перезапускаются.
   Мутант **killed**, если упал хотя бы один тест, прошедший на baseline;
   **no_coverage**, если ни один запрос не попал в мутированную операцию.

| Поле                   | Тип                       | По умолчанию        |
|------------------------|---------------------------|---------------------|
| `tests`                | `TestSuiteSource`         | обязательно         |
| `requirements`         | `list[str]`               | `[]`                |
| `base_url_env`         | str                       | `"ARQA_BASE_URL"`   |
| `pytest_args`          | `list[str]`               | `[]`                |
| `operators`            | `list[MutationOperator]`  | все 3               |
| `max_mutants`          | int \| null               | `null`              |
| `per_mutant_timeout_s` | float, (0, 3600]          | `120.0`             |
| `seed`                 | int \| null               | `null`              |
| `operations`           | `OperationFilter \| null` | `null`              |

`TestSuiteSource` (дискриминатор `kind`):

| kind     | Поля                                                       |
|----------|------------------------------------------------------------|
| `inline` | `files: dict[relpath, content]`                            |
| `git`    | `repo_url`, `ref` (`"HEAD"`), `subdir` (`"."`)             |

`MutationOperator`: `change_field_type`, `remove_required_field`, `replace_status_code`.

Детали реализации, важные для агента:

- `replace_status_code`: 2xx/3xx → 500, 4xx/5xx → 200; ответ `default` не мутируется.
- Поля берутся из JSON-схемы ответа (с `$ref` и `allOf`, до 4 уровней вложенности).
  В `Mutant.location` элемент массива обозначается `*`: `/*/id` — поле `id` в каждом
  элементе массива.
- Мутантные прогоны запускают только тесты, прошедшие baseline (`--deselect` для остальных).
- `no_coverage`: на baseline тесты ни разу не получили ответ, который ломает мутант,
  или мутация ни разу не применилась (например, необязательного поля не было в ответе).
  Если на baseline не прошёл ни один тест, все мутанты — `no_coverage`.
- Ошибки сбора тестов не останавливают pytest (`--continue-on-collection-errors`).
- Если baseline не уложился в `per_mutant_timeout_s`, инструмент завершается с `error`
  (`timeout`); если pytest не смог запустить набор (например, неизвестный флаг в
  `pytest_args`) — `error` с `test_suite_invalid` и хвостом вывода в `details.output_tail`.

### Пример запроса

```json
POST /api/v1/runs
Idempotency-Key: 2f0c1a1e-run-17

{
  "contract": {"kind": "url", "url": "http://petstore:8080/openapi.json"},
  "target": {
    "base_url": "http://petstore:8080",
    "headers": {"Authorization": "Bearer demo-token"}
  },
  "tools": [
    {
      "tool": "schemathesis",
      "phases": ["examples", "coverage", "fuzzing"],
      "max_examples": 50,
      "seed": 42,
      "operations": {"exclude": {"methods": ["DELETE"]}}
    },
    {"tool": "microcks", "timeout_ms": 5000},
    {
      "tool": "mutation",
      "tests": {
        "kind": "inline",
        "files": {
          "tests/conftest.py": "import os, httpx, pytest\n\n@pytest.fixture\ndef client():\n    return httpx.Client(base_url=os.environ['ARQA_BASE_URL'])\n",
          "tests/test_pets.py": "def test_list_pets(client):\n    r = client.get('/pets')\n    assert r.status_code == 200\n    assert isinstance(r.json(), list)\n"
        }
      },
      "max_mutants": 200,
      "seed": 42
    }
  ],
  "timeout_s": 1800,
  "labels": {"agent_session": "s-81", "service": "petstore", "iteration": "3"}
}
```

### Ответ `202 Accepted` — `RunCreated`

Заголовок `Location: /api/v1/runs/{run_id}`.

```json
{
  "run_id": "7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31",
  "state": "queued",
  "created_at": "2026-10-03T12:00:00Z",
  "links": {
    "self": "/api/v1/runs/7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31",
    "result": "/api/v1/runs/7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31/result",
    "cancel": "/api/v1/runs/7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31/cancel"
  }
}
```

---

## GET /api/v1/runs/{run_id} — `RunStatus`

Лёгкий ответ для поллинга (рекомендуемый интервал 2–5 с). `404 run_not_found`.

```json
{
  "run_id": "7d1c3b0e-4f6a-4b8e-9d1a-0c2e5f7a9b31",
  "state": "running",
  "created_at": "2026-10-03T12:00:00Z",
  "started_at": "2026-10-03T12:00:01Z",
  "finished_at": null,
  "tools": [
    {"tool": "schemathesis", "state": "passed", "started_at": "2026-10-03T12:00:01Z", "finished_at": "2026-10-03T12:01:40Z", "progress": 1.0, "error": null},
    {"tool": "microcks", "state": "error", "started_at": "2026-10-03T12:00:01Z", "finished_at": "2026-10-03T12:00:03Z", "progress": null,
     "error": {"code": "tool_failed", "message": "Microcks returned 500 on artifact upload", "details": {}}},
    {"tool": "mutation", "state": "running", "started_at": "2026-10-03T12:00:01Z", "finished_at": null, "progress": 0.35, "error": null}
  ],
  "labels": {"agent_session": "s-81", "service": "petstore", "iteration": "3"},
  "error": null,
  "links": {"self": "...", "result": "...", "cancel": "..."}
}
```

## POST /api/v1/runs/{run_id}/cancel

`202 RunStatus` (state станет `cancelled` асинхронно). Если run уже в терминальном
статусе → `409 run_already_finished`.

---

## GET /api/v1/runs/{run_id}/result — `RunResult`

Доступен только в терминальном статусе (`completed`, `failed`, `cancelled`,
`timed_out`), иначе `409 run_not_finished`. Для `cancelled`/`timed_out` содержит
частичные результаты уже отработавших инструментов.

Верхний уровень:

| Поле              | Тип                      | Описание                                     |
|-------------------|--------------------------|----------------------------------------------|
| `run_id`, `state`, `created_at`, `started_at`, `finished_at`, `duration_s`, `labels` | | |
| `contract`        | `ContractInfo \| null`   | `title`, `version`, `openapi_version`, `operations_total`, `sha256` |
| `target_base_url` | str                      |                                              |
| `tools`           | `list[ToolResult]`       | дискриминатор `tool`, порядок как в запросе  |
| `error`           | `ErrorInfo \| null`      | при `failed` / `timed_out`                   |

Общие поля любого `ToolResult`: `state`, `tool_version`, `started_at`,
`finished_at`, `duration_s`, `error`, `artifacts: list[ArtifactRef]`.

### Общие объекты

**`Finding`** — единый формат находки для schemathesis и microcks:

```json
{
  "id": "st-5f2a91",
  "tool": "schemathesis",
  "check": "response_schema_conformance",
  "severity": "high",
  "operation": {"method": "GET", "path": "/pets/{petId}", "operation_id": "getPet"},
  "title": "Response violates schema",
  "message": "'id' is a required property",
  "request": {"method": "GET", "url": "http://petstore:8080/pets/0", "headers": {"Authorization": "***"}, "body": null},
  "response": {"status_code": 200, "headers": {"content-type": "application/json"}, "body": "{\"name\":\"rex\"}", "elapsed_ms": 12.4},
  "reproduce": "curl -X GET -H 'Authorization: ***' http://petstore:8080/pets/0"
}
```

Рекомендуемый маппинг severity:

| severity   | checks                                                                                   |
|------------|------------------------------------------------------------------------------------------|
| `critical` | `not_a_server_error`, `ignored_auth`                                                     |
| `high`     | `response_schema_conformance`, `status_code_conformance`, `use_after_free`, `ensure_resource_availability`, проваленный шаг Microcks |
| `medium`   | `negative_data_rejection`, `positive_data_acceptance`, `missing_required_header`, `unsupported_method`, `content_type_conformance` |
| `low`      | `response_headers_conformance`, `max_response_time`                                      |

**`ContractCoverage`** — для метрики Contract Coverage:

```json
{
  "operations_total": 8,
  "operations_tested": 7,
  "operations_ratio": 0.875,
  "status_codes_documented": 19,
  "status_codes_observed": 14,
  "status_codes_ratio": 0.7368,
  "undocumented_status_codes_observed": 2,
  "by_operation": [
    {
      "operation": {"method": "GET", "path": "/pets/{petId}", "operation_id": "getPet"},
      "tested": true,
      "documented_status_codes": ["200", "404", "default"],
      "observed_status_codes": [200, 404, 422],
      "requests": 312
    }
  ]
}
```

`status_codes_observed` считает только задокументированные коды (`default` и `4XX`
закрываются любым подходящим наблюдаемым кодом); `422` в примере попадёт в
`undocumented_status_codes_observed`, если не описан.

### `SchemathesisResult`

```json
{
  "tool": "schemathesis",
  "state": "failed",
  "tool_version": "4.1.0",
  "started_at": "2026-10-03T12:00:01Z",
  "finished_at": "2026-10-03T12:01:40Z",
  "duration_s": 99.2,
  "error": null,
  "seed": 42,
  "summary": {
    "operations_selected": 7,
    "operations_tested": 7,
    "test_cases": 1840,
    "requests": 2105,
    "failures": 3,
    "failures_by_check": {"response_schema_conformance": 2, "not_a_server_error": 1},
    "errors": 0
  },
  "coverage": { "...": "ContractCoverage" },
  "findings": [ { "...": "Finding" } ],
  "artifacts": [
    {"name": "schemathesis-junit.xml", "media_type": "application/xml", "size_bytes": 20481, "url": "/api/v1/runs/7d1c.../artifacts/schemathesis-junit.xml"},
    {"name": "schemathesis-events.ndjson", "media_type": "application/x-ndjson", "size_bytes": 912004, "url": "/api/v1/runs/7d1c.../artifacts/schemathesis-events.ndjson"}
  ]
}
```

### `MicrocksResult`

```json
{
  "tool": "microcks",
  "state": "failed",
  "tool_version": "1.12.0",
  "duration_s": 4.1,
  "error": null,
  "test_id": "66fe1b2c9a",
  "test_url": "http://microcks:8080/#/tests/66fe1b2c9a",
  "service_ref": "Petstore API:1.0.0",
  "summary": {"operations_total": 5, "operations_passed": 4, "operations_failed": 1, "steps_total": 9, "steps_passed": 8},
  "operations": [
    {
      "operation": {"method": "GET", "path": "/pets/{petId}", "operation_id": "getPet"},
      "microcks_operation": "GET /pets/{petId}",
      "success": false,
      "elapsed_ms": 31,
      "steps": [
        {"request_name": "rex", "success": true, "elapsed_ms": 14, "message": null},
        {"request_name": "unknown", "success": false, "elapsed_ms": 17, "message": "Response code 500 not in documented codes"}
      ]
    }
  ],
  "coverage": { "...": "ContractCoverage" },
  "findings": [ { "...": "Finding, check = microcks runner, по одному на проваленный step" } ],
  "artifacts": [{"name": "microcks-test-result.json", "media_type": "application/json", "size_bytes": 8120, "url": "..."}]
}
```

### `MutationResult`

```json
{
  "tool": "mutation",
  "state": "failed",
  "duration_s": 612.0,
  "error": null,
  "seed": 42,
  "baseline": {
    "tests_total": 40,
    "passed": 36,
    "failed": 3,
    "errors": 1,
    "skipped": 0,
    "collection_errors": [],
    "runnability": 0.9,
    "duration_s": 7.8,
    "tests": [
      {"node_id": "tests/test_pets.py::test_list_pets", "outcome": "passed", "duration_s": 0.04, "message": null},
      {"node_id": "tests/test_pets.py::test_create_pet_invalid", "outcome": "failed", "duration_s": 0.05, "message": "assert 400 == 422"}
    ]
  },
  "summary": {
    "mutants_total": 120,
    "killed": 81,
    "survived": 27,
    "timeout": 2,
    "no_coverage": 8,
    "errors": 2,
    "mutation_score": 0.7333,
    "by_operator": {"change_field_type": 0.81, "remove_required_field": 0.64, "replace_status_code": 0.76}
  },
  "mutants": [
    {
      "id": "m-0017",
      "operator": "remove_required_field",
      "operation": {"method": "GET", "path": "/pets/{petId}", "operation_id": "getPet"},
      "status_code": "200",
      "location": "/name",
      "description": "removed required field /name",
      "state": "survived",
      "killed_by": [],
      "duration_s": 4.2
    }
  ],
  "artifacts": [
    {"name": "mutation-baseline-junit.xml", "media_type": "application/xml", "size_bytes": 5120, "url": "..."},
    {"name": "mutation-pytest.log", "media_type": "text/plain", "size_bytes": 88012, "url": "..."}
  ]
}
```

Формулы:

- `runnability = passed / tests_total` (тесты с `collection_errors` входят в `tests_total` как `errors`).
- `mutation_score = (killed + timeout) / (mutants_total − errors − no_coverage)`; при нулевом знаменателе — `0`.
- `state = passed`, если `runnability == 1` и `survived == 0`; иначе `failed`.

---

## GET /api/v1/tools — `list[ToolInfo]`

```json
[
  {"tool": "schemathesis", "available": true, "version": "4.1.0", "description": "Property-based API fuzzing", "config_schema": {"...": "JSON Schema SchemathesisConfig"}},
  {"tool": "microcks", "available": false, "version": null, "description": "Contract testing by OpenAPI examples", "config_schema": {"...": "JSON Schema MicrocksConfig"}},
  {"tool": "mutation", "available": true, "version": "0.1.0", "description": "Contract-level mutation testing of pytest suites", "config_schema": {"...": "JSON Schema MutationConfig"}}
]
```

`config_schema` = `Model.model_json_schema()` — агент может использовать его как
описание tool-аргументов для LLM.

## GET /health — `HealthResponse`

```json
{"status": "degraded", "version": "0.1.0", "tools": {"schemathesis": true, "microcks": false, "mutation": true}}
```

Всегда `200`; `degraded`, если хотя бы один инструмент недоступен.

---

## Ошибки — `ProblemDetail` (RFC 9457)

```json
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json

{
  "type": "about:blank",
  "title": "Validation error",
  "status": 422,
  "detail": "Request body does not match RunCreateRequest",
  "code": "validation_error",
  "errors": [
    {"loc": ["body", "tools", 0, "schemathesis", "max_examples"], "msg": "Input should be less than or equal to 10000", "type": "less_than_equal"}
  ]
}
```

| HTTP | `code`                 | Когда                                                     |
|------|------------------------|-----------------------------------------------------------|
| 404  | `run_not_found`        | неизвестный `run_id`                                      |
| 404  | `artifact_not_found`   | неизвестный артефакт                                      |
| 409  | `run_not_finished`     | `/result` до терминального статуса                        |
| 409  | `run_already_finished` | `/cancel` завершённого прогона                            |
| 422  | `validation_error`     | тело не прошло схему                                      |
| 422  | `contract_unreachable` | контракт не скачался по `url`                             |
| 422  | `contract_invalid`     | не OpenAPI 3.x / битый документ                           |
| 422  | `test_suite_invalid`   | некорректный `tests` у `mutation`                         |
| 429  | `too_many_runs`        | очередь заполнена, есть `Retry-After`                     |
| 500  | `internal_error`       |                                                           |
| 503  | `tool_unavailable`     | запрошенный инструмент сейчас недоступен                  |

Коды, которые встречаются только внутри `ErrorInfo` прогона/инструмента:
`target_unreachable`, `tool_failed`, `timeout`, `test_suite_invalid`
(например, не склонировался git-репозиторий).

---

## Открытые вопросы

- Где живут сгенерированные тесты: агент шлёт их `inline` или пушит в git и
  передаёт `git`? Сейчас поддерживаются оба варианта.
- Нужен ли `kind: "path"` для контракта/тестов при общем volume между агентом и
  testing_tools (сейчас не предусмотрен — сервисы считаются разнесёнными).
- Webhook по завершению прогона вместо поллинга — не предусмотрен, можно добавить
  `callback_url` в `RunCreateRequest` без ломки контракта.
- EvoMaster / RESTler как дополнительные baseline — добавляются новым членом
  `ToolConfig` / `ToolResult` с новым значением `tool`.
