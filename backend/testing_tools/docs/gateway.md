# testing_tools — устройство HTTP-шлюза (v0)

Документ для тех, кто впервые открывает код шлюза, — людей и LLM-агентов. Он описывает,
как шлюз устроен внутри. Что шлюз обещает снаружи (эндпоинты, поля, коды ошибок),
описано в [`API.md`](API.md), а Pydantic-модели лежат в
[`schemas.py`](../src/testing_tools/api/schemas.py). Здесь это не повторяется.

## Коротко

- FastAPI-приложение, которое реализует контракт `API.md` под префиксом `/api/v0`.
- HTTP-слой настоящий: валидация, ProblemDetail-ошибки, загрузка и разбор контракта,
  idempotency, 429, асинхронный жизненный цикл прогона, отмена, таймаут, артефакты.
- Инструменты (Schemathesis, Microcks, мутации) **не запускаются**. Их подменяет
  `StubEngine` + `stub_results.py`: каждый инструмент «работает» `TT_TOOL_DELAY_S` секунд
  и возвращает правдоподобный результат, посчитанный по контракту.
- Всё состояние хранится в памяти процесса и пропадает при рестарте.
- Python 3.14, `mypy --strict`, тесты на pytest. Комментарии в коде — на английском.

## Карта модулей

Все модули лежат в `src/testing_tools/`.

| Модуль                | Отвечает за                                                                 |
|-----------------------|------------------------------------------------------------------------------|
| `__init__.py`         | `main()` — точка входа `uv run testing-tools`: настраивает logging, запускает uvicorn с фабрикой `create_app`, адрес берёт из `TT_HOST`/`TT_PORT` |
| `api/schemas.py`      | Контракт: модели запросов и ответов, enum'ы, `ErrorCode`. **Источник истины — не менять без правки `API.md`** |
| `app/main.py`         | `create_app(settings, transport)` — собирает приложение: state, обработчики ошибок, роутер, `/health`, lifespan |
| `app/settings.py`     | `Settings` (frozen dataclass) и `Settings.from_env()` — чтение `TT_*`      |
| `app/routes.py`       | Все эндпоинты под префиксом, зависимости `SettingsDep`/`StoreDep`/`EngineDep`/`HttpDep` |
| `app/errors.py`       | `ApiError` и обработчики, которые превращают исключения в `application/problem+json` |
| `app/validation.py`   | Проверки тела запроса, которые не выражаются Pydantic-схемой               |
| `app/contract.py`     | Загрузка OpenAPI (url/inline), разбор, список операций, `OperationFilter`  |
| `app/store.py`        | `RunRecord`/`ToolRun` — изменяемое состояние прогона; `RunStore` — реестр в памяти; `*_view()` — сборка ответов API |
| `app/engine.py`       | `StubEngine` — asyncio-задача на прогон: смена состояний, прогресс, отмена, таймаут, артефакты |
| `app/stub_results.py` | `build_result()` — заглушечный `ToolResult` для каждого инструмента        |

Зависимости между модулями идут сверху вниз: `routes` → `validation`/`contract`/`store`/`engine`;
`engine` → `store`/`stub_results`; `stub_results` → `contract`; все модули → `schemas`.
Циклических импортов нет.

## Сборка приложения (`app/main.py`)

`create_app()` кладёт в `app.state`:

- `settings` — `Settings`;
- `store` — `RunStore()`;
- `engine` — `StubEngine(settings)`;
- `http` — `httpx2.AsyncClient`. Создаётся в lifespan, через него скачиваются контракты.
  Параметр `transport` подменяет сеть в тестах (`httpx2.MockTransport`).

При остановке lifespan отменяет все незавершённые задачи прогонов и ждёт их.

Маршруты получают объекты из `app.state` через `Depends`, глобальных синглтонов нет.
Поэтому каждый `create_app()` в тестах полностью изолирован.

## Путь запроса `POST /runs`

Порядок шагов в `routes.create_run` важен: дешёвые проверки идут раньше дорогих,
а порядок кодов ошибок совпадает с `API.md`.

1. FastAPI валидирует тело по `RunCreateRequest`. При ошибке — `422 validation_error`
   со списком `errors`: `loc`, `msg` и `type` берутся из Pydantic как есть.
2. Если `Idempotency-Key` уже встречался, возвращается тот же `RunCreated`. Дальнейшие
   проверки не выполняются, новый прогон не создаётся.
3. Если активных (нетерминальных) прогонов уже `max_active_runs` — `429 too_many_runs`
   с `Retry-After`.
4. `check_path_regexes` — каждый `path_regex` из фильтров должен компилироваться,
   иначе `422 validation_error`. Этой проверки нет в `API.md`; без неё битый regex дал бы 500.
5. `load_contract` → `422 contract_unreachable` / `422 contract_invalid`.
6. `check_tools_available` → `503 tool_unavailable`, в `detail` перечислены недоступные инструменты.
7. `check_test_suites` — для `mutation` с inline-тестами пути должны быть непустыми,
   относительными и без `..` (проверяются и POSIX-, и Windows-варианты), иначе
   `422 test_suite_invalid`.
8. `RunRecord.new()` → `store.add()` → `engine.start()`. Ответ — `202 RunCreated`
   с заголовком `Location`.

Остальные маршруты тонкие: находят запись через `_record()` (неизвестный или не-UUID
`run_id` → `404 run_not_found`), проверяют состояние и возвращают `*_view()`.

| Маршрут                         | Логика                                                               |
|---------------------------------|----------------------------------------------------------------------|
| `GET /runs/{id}`                | `record.status_view()`                                               |
| `GET /runs/{id}/result`         | не терминальный → `409 run_not_finished`, иначе `record.result_view()` |
| `POST /runs/{id}/cancel`        | терминальный → `409 run_already_finished`, иначе `engine.cancel()` и `202` с текущим статусом (отмена применяется асинхронно) |
| `GET /runs/{id}/artifacts/{n}`  | байты из `record.artifacts[n]` с их `media_type`, иначе `404 artifact_not_found` |
| `GET /tools`                    | статичный список; `config_schema` = `Model.model_json_schema()`       |
| `GET /health`                   | `ok`, если все инструменты доступны, иначе `degraded`; всегда `200`   |

## Ошибки (`app/errors.py`)

- Код бросает `ApiError(status, code, detail, headers)`. `title` выводится из `code`:
  `run_not_found` → «Run not found».
- `RequestValidationError` → `422 validation_error`. Так же можно сообщить и свою ошибку
  уровня поля: см. `check_path_regexes`.
- Любое другое исключение → `500 internal_error` без текста исключения в ответе
  (полный traceback пишется в лог).
- Неизвестный маршрут или неподдерживаемый метод отдают стандартный ответ FastAPI,
  потому что в `ErrorCode` нет подходящего кода.

## Контракт (`app/contract.py`)

- `load_contract(source, client)`: inline-контракт сразу уходит на разбор, url скачивается
  с заголовками из `source.headers` (таймаут 10 с). Сетевая ошибка или не-2xx →
  `contract_unreachable`.
- `parse_contract(text)`: сначала пробует JSON, потом YAML. Требует корень-объект,
  `openapi: "3.*"`, `info.title`, `info.version`; `paths` — объект или отсутствует.
  Нарушение → `contract_invalid`. `sha256` считается по исходному тексту.
- Результат — `LoadedContract(info: ContractInfo, operations: tuple[ContractOperation, ...])`.
  `ContractOperation` хранит `ref: OperationRef`, `tags` и `status_codes` — ключи
  `responses` строками, как в контракте (`"200"`, `"4XX"`, `"default"`).
  Операции идут в порядке `paths`, а внутри пути — в порядке `HttpMethod`.
- `select_operations(operations, filter)` реализует семантику `OperationFilter` из `API.md`.
  Matcher без единого критерия считается отсутствующим, иначе пустой `exclude`
  исключал бы всё.

## Состояние прогона (`app/store.py`)

- `RunRecord` — изменяемый dataclass, единственное место, где хранится состояние прогона:
  исходный `request`, `contract`, `links`, состояние и времена, `error`, `tools: list[ToolRun]`,
  `artifacts: dict[name, Artifact]`, `task`.
- `ToolRun` — состояние одного инструмента: `config`, `state`, времена, `progress`, `error`, `result`.
- Ответы API собираются только через `*_view()`: `created_view`, `status_view`, `result_view`.
  Если инструмент не дал результата (отменён, пропущен, прерван), `ToolRun.result_view()`
  возвращает пустой `ToolResult` нужного типа с текущим `state`.
- `RunStore` хранит `dict[UUID, RunRecord]` и `dict[idempotency_key, UUID]`, без TTL и
  без блокировок: всё выполняется в одном event loop.

## Движок (`app/engine.py`)

- `start(record)` создаёт `asyncio.Task` с `run(record, record.request.timeout_s)`.
- `run()`: `RUNNING` → инструменты по очереди внутри `asyncio.timeout(timeout_s)` → `COMPLETED`.
  - Таймаут: прерванный инструмент → `error` с `code=timeout`, не начатые → `skipped`,
    прогон → `TIMED_OUT` с `error`.
  - Отмена (`CancelledError`): работающий инструмент → `cancelled`, не начатые → `skipped`,
    прогон → `CANCELLED`, исключение пробрасывается дальше.
- `cancel(record)`: если прогон ещё `QUEUED`, состояние проставляется сразу, потому что
  задача, отменённая до первого шага, свой `except` не выполнит. Потом `task.cancel()`.
- `_run_tool()`: прогресс растёт за 10 шагов по `tool_delay_s / 10`, затем
  `build_result()`, артефакт `<tool>-stub.json` (JSON результата) и `ArtifactRef`
  в `result.artifacts`. Итоговый `ToolState` берётся из результата.
- Инструменты одного прогона идут последовательно, прогоны между собой — параллельно.

## Заглушечные результаты (`app/stub_results.py`)

`build_result(config, contract, version, started_at, finished_at)` выбирает построитель
по типу конфига (`match`) и добавляет общие поля: версию, времена, `duration_s`.

| Инструмент     | Что возвращает                                                              |
|----------------|------------------------------------------------------------------------------|
| `schemathesis` | `passed`, без находок. Выбранные фильтром операции «протестированы»; `test_cases` = `requests` = `max_examples × операций`; `seed` — из конфига или случайный |
| `microcks`     | `passed`. По операции на каждую операцию контракта (или из `filtered_operations`, имена вида `"GET /pets"`), один успешный шаг на операцию; `service_ref` = `name:version` из конфига или `info` |
| `mutation`     | baseline — по одному прошедшему тесту `path::test_stub` на inline-файл `test_*.py`; мутантов 0, `mutation_score = 0`. Для `git`-набора тестов нет → `runnability = 0` → `failed` (по формуле из `API.md`) |

Покрытие (`_coverage`) считается по всему контракту. Для «протестированных» операций
наблюдаемым считается каждый явный код (`"404"` → 404) и диапазон (`"4XX"` → 400);
`default` остаётся непокрытым. Недокументированных кодов нет.

## Настройки

Переменные `TT_*` перечислены в [README](../README.md). `Settings` — frozen dataclass,
в тестах его создают напрямую: `Settings(tool_delay_s=0, ...)`.

## Тесты

Запуск — `uv run pytest` и `uv run mypy src tests` из `backend/testing_tools`.

| Файл                       | Что покрывает                                                        |
|----------------------------|----------------------------------------------------------------------|
| `conftest.py`              | фикстура `client` — `TestClient` поверх `create_app(Settings(tool_delay_s=0))`; `anyio_backend` для async-тестов |
| `petstore.py`              | эталонный контракт (4 операции) и `inline_contract()`, общие для всех тестов |
| `test_settings.py`, `test_health.py`, `test_tools_endpoint.py`, `test_entrypoint.py` | настройки, `/health`, `/tools`, `main()` |
| `test_errors.py`           | формат ProblemDetail на временных маршрутах внутри теста             |
| `test_contract.py`         | разбор контракта, загрузка по url через `MockTransport`, фильтр операций |
| `test_runs_create.py`      | `POST /runs` и все проверки до ответа 202, `GET /runs/{id}`; хелпер `run_request()` |
| `test_engine.py`           | движок напрямую (async): результаты, покрытие, артефакты, таймаут, отмена |
| `test_runs_lifecycle.py`   | сквозные сценарии через HTTP с поллингом (`wait_for`): result, cancel, artifacts |

Приёмы:
- `TestClient` используется только как контекст-менеджер (`with`): тогда event loop живёт
  между запросами и фоновые задачи прогонов не умирают.
- Если прогон должен оставаться «в работе», нужен `Settings(tool_delay_s=60)`.
  Таймаут прогона через HTTP не проверить (минимум `timeout_s` — 10 с), поэтому он
  проверяется в `test_engine.py` вызовом `engine.run(record, timeout_s=0.05)`.

## Как менять

- **Новое поле или эндпоинт в контракте**: правка `schemas.py` и `API.md`, потом тест,
  потом маршрут в `routes.py` и при необходимости `*_view()` в `store.py`.
- **Новая проверка запроса**: функция в `validation.py`, вызов в `create_run` на месте,
  соответствующем порядку из `API.md`.
- **Сменить префикс** (`v0` → `v1`): `TT_API_PREFIX` или значение по умолчанию в `Settings`;
  `links` и `Location` строятся от префикса.
- **Подключить настоящий инструмент**: шлюзу важно только то, что движок заполняет
  `ToolRun.state/progress/result/error` и `record.artifacts`. Реальный раннер может
  заменить `build_result()` для своего инструмента внутри `_run_tool`, не трогая маршруты
  и `store.py`. Проверка доступности — `Settings.tool_available()`, сейчас она статична.
- **Хранилище вместо памяти**: интерфейс `RunStore` маленький (`add`, `get`,
  `by_idempotency_key`, `active_count`, `all`), но `RunRecord` движок меняет на месте,
  так что при переезде в БД нужен явный save после изменений.

## Отличия от `API.md`

- Префикс `/api/v0` вместо `/api/v1`.
- Неизвестный маршрут или метод — стандартный ответ FastAPI, не ProblemDetail.
- Невалидный `path_regex` → `422 validation_error` (в доке не описано).
- Нет 24-часового хранения: прогоны и idempotency-ключи живут, пока жив процесс.
- Маскировать заголовки негде: заглушка не возвращает `Finding` с запросами.
