# testing_tools — устройство HTTP-шлюза (v0)

Документ для тех, кто впервые открывает код шлюза, — людей и LLM-агентов. Он описывает,
как шлюз устроен внутри. Что шлюз обещает снаружи (эндпоинты, поля, коды ошибок),
описано в [`API.md`](API.md), а Pydantic-модели лежат в
[`schemas.py`](../src/testing_tools/api/schemas.py). Здесь это не повторяется.

## Коротко

- FastAPI-приложение, которое реализует контракт `API.md` под префиксом `/api/v0`.
- HTTP-слой настоящий: валидация, ProblemDetail-ошибки, загрузка и разбор контракта,
  idempotency, 429, асинхронный жизненный цикл прогона, отмена, таймаут, артефакты.
- `Engine` передаёт каждый инструмент его **раннеру** (`app/tools/`). В режиме
  `TT_TOOL_MODE=real` (по умолчанию) это настоящие Schemathesis, Microcks и мутационный
  раннер. В режиме `stub` все инструменты подменяет `StubRunner` + `stub_results.py`:
  инструмент «работает» `TT_TOOL_DELAY_S` секунд и возвращает правдоподобный результат.
- Всё состояние хранится в памяти процесса и пропадает при рестарте.
- Настройки — `TT_*` из окружения или `.env`, шаблон — `.env.example`.
- Python 3.14, `mypy --strict`, тесты на pytest. Комментарии в коде — на английском.

## Карта модулей

Все модули лежат в `src/testing_tools/`.

| Модуль                | Отвечает за                                                                 |
|-----------------------|------------------------------------------------------------------------------|
| `__init__.py`         | `main()` — точка входа `uv run testing-tools`: настраивает logging, запускает uvicorn с фабрикой `create_app`, адрес берёт из `Settings` (`host`/`port`) |
| `api/schemas.py`      | Контракт: модели запросов и ответов, enum'ы, `ErrorCode`. **Источник истины — не менять без правки `API.md`** |
| `app/main.py`         | `create_app(settings, transport)` — собирает приложение: state, обработчики ошибок, роутер, `/health`, lifespan |
| `app/settings.py`     | `Settings` — `pydantic-settings`, читает `TT_*` из окружения и `.env`      |
| `app/routes.py`       | Все эндпоинты под префиксом, зависимости `SettingsDep`/`StoreDep`/`EngineDep`/`HttpDep` |
| `app/errors.py`       | `ApiError` и обработчики, которые превращают исключения в `application/problem+json` |
| `app/validation.py`   | Проверки тела запроса, которые не выражаются Pydantic-схемой               |
| `app/contract.py`     | Загрузка OpenAPI (url/inline), разбор, список операций, `OperationFilter`  |
| `app/store.py`        | `RunRecord`/`ToolRun` — изменяемое состояние прогона; `RunStore` — реестр в памяти; `*_view()` — сборка ответов API |
| `app/engine.py`       | `Engine` — asyncio-задача на прогон: проверка target, смена состояний, вызов раннеров, ошибки инструментов, отмена, таймаут, артефакты |
| `app/stub_results.py` | `build_result()` — заглушечный `ToolResult` для каждого инструмента        |
| `app/tools/__init__.py` | `default_runners(settings)` — раннер на каждый `ToolName` по `tool_mode`  |
| `app/tools/base.py`   | `ToolRunner` (протокол), `ToolContext` (что раннер получает от движка), `ToolError` |
| `app/tools/common.py` | общее для раннеров: `OperationIndex` (путь → операция), `Observations` + `build_coverage`, маскирование, curl, `filtered_document`, `resolve` для `$ref` |
| `app/tools/process.py`| `run_process()` — подпроцесс в своей группе; убивается целиком по таймауту и при отмене |
| `app/tools/stub.py`   | `StubRunner` — обёртка над `build_result()` с задержкой                     |
| `app/tools/schemathesis.py` | `SchemathesisRunner` — `st run` подпроцессом, разбор JSON/NDJSON-отчётов |
| `app/tools/microcks.py` | `MicrocksRunner` — клиент REST API Microcks                               |
| `app/tools/mutation/` | `MutationRunner`: `mutants.py` (генерация и применение), `proxy.py` (мутирующий прокси), `suite.py` (набор тестов, окружение, pytest), `arqa_report_plugin.py` (pytest-плагин отчёта) |

Зависимости между модулями идут сверху вниз: `routes` → `validation`/`contract`/`store`/`engine`;
`engine` → `store`/`tools`; `tools` → `contract`/`settings`/`store`/`stub_results`;
все модули → `schemas`.
Циклических импортов нет.

## Сборка приложения (`app/main.py`)

`create_app()` кладёт в `app.state`:

- `settings` — `Settings`;
- `store` — `RunStore()`;
- `engine` — `Engine(settings)`, раннеры выбираются по `settings.tool_mode`;
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
6. `check_tools_available(body, settings.down_tools())` → `503 tool_unavailable`, в `detail`
   перечислены недоступные инструменты. Доступность считает `Settings.tool_available()`:
   инструмент не в `TT_UNAVAILABLE_TOOLS`, и в режиме `real` для Schemathesis установлен
   пакет, а для Microcks задан `TT_MICROCKS_URL`.
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
- Результат — `LoadedContract(info: ContractInfo, operations: tuple[ContractOperation, ...],
  document: dict)`. `document` — разобранный документ как есть; из него раннеры берут
  схемы ответов и собирают отфильтрованный контракт.
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

- `Engine(settings, runners=None)`; `runners` по умолчанию — `default_runners(settings)`,
  в тестах можно подставить свои.
- `start(record)` создаёт `asyncio.Task` с `run(record, record.request.timeout_s)`.
- `run()`: `RUNNING` → (в режиме `real`) проверка target → инструменты по очереди внутри
  `asyncio.timeout(timeout_s)` → `COMPLETED`.
  - Проверка target: `GET base_url`, до 3 попыток с паузой 1 с. Годится любой HTTP-ответ,
    даже 404. Если соединиться не удалось — прогон `FAILED` с `target_unreachable`,
    инструменты `skipped`.
  - Таймаут: прерванный инструмент → `error` с `code=timeout`, не начатые → `skipped`,
    прогон → `TIMED_OUT` с `error`.
  - Отмена (`CancelledError`): работающий инструмент → `cancelled`, не начатые → `skipped`,
    прогон → `CANCELLED`, исключение пробрасывается дальше.
- `cancel(record)`: если прогон ещё `QUEUED`, состояние проставляется сразу, потому что
  задача, отменённая до первого шага, свой `except` не выполнит. Потом `task.cancel()`.
- `_run_tool()`: создаёт временный каталог (`TT_WORK_DIR`), собирает `ToolContext` и
  вызывает `runner.run(config, ctx)`.
  - `ToolError` → инструмент `error` с кодом и `details` из исключения.
  - Любое другое исключение → `error` с `tool_failed` и `"<Тип>: <текст>"`, traceback в лог.
  - Остальные инструменты прогона продолжают работать, прогон всё равно `COMPLETED`.
  - Временный каталог удаляется всегда (и при отмене), если не `TT_KEEP_WORK_DIRS=true`.
  - Движок сам заполняет `started_at`, `finished_at`, `duration_s` и `artifacts`
    (из вызовов `ctx.add_artifact`) и ставит `progress=1.0`.
- Инструменты одного прогона идут последовательно, прогоны между собой — параллельно.

## Раннеры (`app/tools/`)

Раннер — объект с `async run(config, ctx) -> ToolResult`. Из `ToolContext` он берёт
контракт (`ctx.contract.document` — разобранный документ), `target`, свой `work_dir`,
настройки и два колбэка: `set_progress(0..1)` и `add_artifact(name, media_type, bytes)`.
Если инструмент не смог отработать, раннер бросает `ToolError(code, message, details)`.
При отмене раннер обязан освободить ресурсы: подпроцессы запускаются через
`run_process()`, который убивает всю группу процессов, а прокси закрывается в `finally`.

### Schemathesis (`schemathesis.py`)

1. Операции выбираются по `OperationFilter` нашей функцией `select_operations`, и в
   `work_dir/openapi.json` пишется копия контракта только с ними. Фильтры Schemathesis
   не используются, потому что их семантика отличается от `API.md`.
2. `python -m schemathesis.cli run` с флагами из конфига (маппинг — в `_command`), отчёты
   `json`, `ndjson`, `junit`, `har` и `--generation-database none`. `seed` передаётся
   всегда: если в конфиге его нет, он выбирается случайно и попадает в результат.
   `max_response_time` в Schemathesis — не проверка, а флаг `--max-response-time`.
3. Код выхода 0 или 1 — нормальный, иначе `ToolError(tool_failed)` с хвостом вывода.
4. `summary` берётся из JSON-отчёта. Находки и покрытие — из событий `ScenarioFinished`
   в NDJSON: каждая проверка со `status=failure` превращается в `Finding`, дубли
   (проверка + операция + заголовок + сообщение) схлопываются, `id` — хэш этой четвёрки.
   Запросы с методом, отличным от метода операции (зонды `unsupported_method`), в
   покрытие не идут. `severity` — по таблице из `API.md`, для неизвестных проверок —
   по оценке самого Schemathesis.

### Microcks (`microcks.py`)

1. Если заданы `TT_MICROCKS_CLIENT_ID`/`SECRET` и в `GET /api/keycloak/config` включена
   авторизация — токен client credentials из Keycloak.
2. Контракт (`info.title`/`info.version` заменяются на `service_name`/`service_version`)
   загружается через `POST /api/artifact/upload`. Ответ — `name:version`.
3. `GET /api/services/{name:version}` — внутренний id для последующего удаления.
4. `POST /api/tests` (`testEndpoint` — `base_url` без завершающего `/`;
   `target.headers` добавляются в `operationsHeaders.globals`), затем опрос
   `GET /api/tests/{id}` каждые `TT_MICROCKS_POLL_INTERVAL_S` до `inProgress=false`.
   Если тест не закончился за `timeout_ms + 60 с` — `ToolError(timeout)`.
5. Для каждой операции — `GET /api/tests/{id}/messages/{testCaseId}`: оттуда берутся
   статусы ответов (для покрытия) и запрос/ответ для находок. `testCaseId` =
   `{id}-{testNumber}-{operationName}`, где `/` заменён на `!`.
6. Каждый проваленный шаг — `Finding` с `check = runner` и `severity=high`.
7. Если `cleanup=true`, сервис удаляется в `finally` (даже при ошибке или отмене).

### Мутации (`mutation/`)

1. `Suite.prepare`: inline-файлы раскладываются в `work_dir/suite`, git-репозиторий
   клонируется (`--depth 1` для `HEAD`, иначе полный clone + checkout). Python — текущий
   интерпретатор, а при непустых `requirements` — venv через `uv` с `pytest`, `httpx`
   и этими пакетами. `requirements`, похожие на опции (`-…`), отклоняются.
2. `generate()` строит мутантов по выбранным операциям. Для каждого документированного
   ответа: `replace_status_code` (2xx/3xx → 500, 4xx/5xx → 200; `default` пропускается),
   а по JSON-схеме ответа (до 4 уровней вложенности, с `$ref` и `allOf`) —
   `remove_required_field` для обязательных полей и `change_field_type` для полей с
   известным типом. Элементы массивов обозначаются `*`: `/*/id`. `max_mutants` — случайная
   выборка по `seed` с сохранением порядка.
3. `MutatingProxy` (ASGI) поднимается на свободном порту `127.0.0.1` через uvicorn.
   Перехват сигналов uvicorn отключён, чтобы не мешать основному серверу. Тесты получают
   `http://127.0.0.1:<port><путь base_url>` в переменной `base_url_env`. Прокси добавляет
   `target.headers`, записывает, какие операции и коды ответов через него прошли, и
   применяет активного мутанта к ответам с его операцией и ключом ответа.
4. pytest запускается с плагином `arqa_report_plugin` (реальные node id и исходы),
   `--continue-on-collection-errors` и `-p no:cacheprovider`; переменные `TT_*` из
   окружения убираются.
5. Baseline. Не уложился в `per_mutant_timeout_s` — `ToolError(timeout)`; pytest не смог
   работать (коды 2–4, нет отчёта) — `ToolError(test_suite_invalid)`.
6. Для каждого мутанта:
   - `no_coverage` без запуска, если на baseline ни один запрос не получил ответ, который
     ломает мутант, или ни один тест не прошёл baseline;
   - иначе тесты перезапускаются с `--deselect` для упавших на baseline;
   - `timeout` — не уложился в `per_mutant_timeout_s`; `error` — pytest сломался;
     `no_coverage` — мутация ни разу не применилась (например, поля нет в ответе);
     `killed` — упал хотя бы один тест, прошедший baseline; иначе `survived`.
7. Формулы `runnability`, `mutation_score` и итоговый `state` — как в `API.md`.

## Заглушечные результаты (`app/stub_results.py`, режим `stub`)

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

## Настройки (`app/settings.py`)

- `Settings` — frozen `BaseSettings` из `pydantic-settings` с `env_prefix="TT_"`.
  Значения берутся из аргументов конструктора, потом из окружения процесса,
  потом из `.env` в **текущем рабочем каталоге**, потом из значений по умолчанию.
- Все ключи с комментариями — в [`.env.example`](../.env.example), таблица — в
  [README](../README.md). Тест `test_env_example_lists_exactly_the_settings` следит,
  чтобы шаблон совпадал с полями `Settings`. Новое поле без строки в `.env.example`
  этот тест уронит.
- `unavailable_tools` в env задаётся строкой через запятую (`NoDecode` + валидатор),
  неизвестное имя инструмента — ошибка при старте.
- `version` — `ClassVar`, из env не настраивается.
- В тестах `Settings(tool_delay_s=0, ...)` создаётся напрямую. Автофикстура
  `isolated_env` в `conftest.py` убирает `TT_*` из окружения, ставит `TT_TOOL_MODE=stub`
  и переходит в `tmp_path`, чтобы локальный `.env` разработчика не влиял на тесты.
  Тестам реальных раннеров режим `real` передаётся явно.

## Тесты

Запуск — `uv run pytest` и `uv run mypy src tests` из `backend/testing_tools`.

| Файл                       | Что покрывает                                                        |
|----------------------------|----------------------------------------------------------------------|
| `conftest.py`              | автофикстура `isolated_env` (без `TT_*` и `.env`, режим `stub`); фикстура `client` — `TestClient` поверх `create_app(Settings(tool_delay_s=0))`; `anyio_backend` для async-тестов; `petstore_url` — `buggy_petstore` на свободном порту (на всю сессию); `contract` — его контракт |
| `buggy_petstore.py`, `petstore_openapi.yaml` | сервис с заложенными нарушениями контракта и его контракт — цель для реальных инструментов |
| `tool_helpers.py`          | `make_ctx()` — `ToolContext` для прямого вызова раннера, запоминает артефакты и прогресс |
| `test_tool_common.py`, `test_tool_mutants.py` | хелперы раннеров, генерация и применение мутантов |
| `test_tool_schemathesis.py` | настоящий `st run` против `buggy_petstore`, маппинг конфига на флаги CLI |
| `test_tool_microcks.py`    | `MicrocksRunner` против фейкового REST API Microcks (`MockTransport`) |
| `test_tool_mutation.py`    | настоящие прокси и pytest против `buggy_petstore`: baseline, мутанты, git-набор, ошибки набора |
| `test_tool_engine_real.py` | движок с подставными раннерами (`ToolError`, падение, проверка target, временные каталоги), `run_process` |
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
- **Новая настройка**: поле в `Settings` и строка в `.env.example` (иначе упадёт тест),
  строка в таблице README.
- **Сменить префикс** (`v0` → `v1`): `TT_API_PREFIX` или значение по умолчанию в `Settings`;
  `links` и `Location` строятся от префикса.
- **Новый инструмент**: конфиг и результат в `schemas.py` и `API.md`, раннер в
  `app/tools/` (протокол `ToolRunner`), строка в `default_runners()`, заглушка в
  `stub_results.py`, доступность в `Settings.tool_available()`.
- **Хранилище вместо памяти**: интерфейс `RunStore` маленький (`add`, `get`,
  `by_idempotency_key`, `active_count`, `all`), но `RunRecord` движок меняет на месте,
  так что при переезде в БД нужен явный save после изменений.

## Отличия от `API.md`

- Префикс `/api/v0` вместо `/api/v1`.
- Неизвестный маршрут или метод — стандартный ответ FastAPI, не ProblemDetail.
- Невалидный `path_regex` → `422 validation_error` (в доке не описано).
- Нет 24-часового хранения: прогоны и idempotency-ключи живут, пока жив процесс.
- В `Finding.request.headers` и `reproduce` маскируются заголовки из `target.headers`,
  а также `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`. Сырые
  артефакты Schemathesis маскирует сам (`--output-sanitize` включён по умолчанию);
  лог pytest не маскируется.
- В `Finding` от Microcks `request.url` — шаблон пути (`/pets/{petId}`): Microcks не
  отдаёт итоговый URL запроса.
- `Finding.check` у Schemathesis может быть проверкой, которой нет в `SchemathesisCheck`
  (например, `allow_header_conformance`): при `checks=null` Schemathesis запускает все
  свои проверки.
