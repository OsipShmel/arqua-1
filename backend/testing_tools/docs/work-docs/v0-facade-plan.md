# testing_tools v0 — FastAPI-фасад: план

Цель: поднять HTTP-сервис ровно по контракту из [`API.md`](../API.md) и
[`schemas.py`](../../src/testing_tools/api/schemas.py), чтобы агент мог интегрироваться
уже сейчас. Инструменты (Schemathesis, Microcks, мутации) **не запускаются** — вместо
них заглушечный движок отдаёт правдоподобные результаты, посчитанные по контракту.

## Что значит «v0»

- Префикс маршрутов — `/api/v0` (в `API.md` — `/api/v1`; `v1` останется за
  реальной реализацией). Префикс — одна настройка, `links` строятся от неё.
- `schemas.py` не меняется: модели запросов/ответов те же, `API_VERSION` не трогаем.
- `/health` — без префикса, как в доке.

## Что настоящее, а что заглушка

| Часть                                                      | v0                       |
|------------------------------------------------------------|--------------------------|
| Все 7 эндпоинтов, коды ответов, `Location`, `Retry-After`  | настоящее                |
| Валидация тела по `RunCreateRequest`, ProblemDetail-ошибки | настоящее                |
| Загрузка контракта (url/inline), разбор OpenAPI 3.x, `ContractInfo` | настоящее       |
| Проверка `mutation.tests` (пустые/абсолютные пути/`..`)    | настоящее                |
| `Idempotency-Key`, лимит активных прогонов → 429           | настоящее (в памяти)     |
| Жизненный цикл queued → running → completed/cancelled/timed_out | настоящее (asyncio) |
| Результаты инструментов                                    | заглушка (см. ниже)      |
| Хранение прогонов                                          | в памяти процесса, без TTL |
| Маскирование заголовков                                    | не нужно: в заглушке нет `Finding` с запросами |

Заглушечные результаты (каждый инструмент — `state=passed`, `findings=[]`):

- `schemathesis` — все выбранные операции «протестированы», наблюдаемые коды =
  задокументированные числовые коды, `ContractCoverage` честно посчитан по контракту.
- `microcks` — по операции на каждую операцию контракта, все успешны.
- `mutation` — baseline: по одному прошедшему тесту на inline-файл `test_*.py`
  (для `git` — 0 тестов), мутантов 0, `mutation_score=0` (формула при нулевом знаменателе).
- Каждый инструмент кладёт артефакт `<tool>-stub.json` (его же `ToolResult`).

`OperationFilter` в заглушке применяется к списку операций контракта (это дёшево и
даёт осмысленные `operations_selected`).

## Устройство

```
backend/testing_tools/
  src/testing_tools/
    api/schemas.py      — контракт (не меняется)
    app/
      settings.py       — Settings: api_prefix, version, tool_delay_s, max_active_runs,
                          unavailable_tools; читаются из env TT_*
      errors.py         — ApiError + обработчики → application/problem+json
      contract.py       — load_contract(): url/inline → LoadedContract (ContractInfo + операции)
      stub_results.py   — построение ToolResult-заглушек по контракту
      store.py          — RunRecord + RunStore (прогоны, idempotency, артефакты)
      engine.py         — StubEngine: asyncio-задача на прогон, отмена, таймаут
      routes.py         — эндпоинты
      main.py           — create_app(settings), app для uvicorn
    __init__.py         — main(): uvicorn на TT_HOST:TT_PORT
  tests/                — pytest, по файлу на модуль/эндпоинт
```

Настройки (env): `TT_API_PREFIX` (`/api/v0`), `TT_TOOL_DELAY_S` (`1.0` — сколько
«работает» каждый инструмент, чтобы поллинг видел `running`), `TT_MAX_ACTIVE_RUNS` (`16`),
`TT_UNAVAILABLE_TOOLS` (через запятую — для проверки `503`/`degraded`), `TT_HOST`, `TT_PORT`.

Зависимости: `fastapi`, `uvicorn`, `httpx2`, `pyyaml`; dev — `pytest`, `mypy` (strict).

## Этапы (каждый — сначала тесты, потом код, коммит в ветку `feat/testing-api-gateway`)

1. **Каркас**: зависимости, pytest, `create_app`, `GET /health` (ok/degraded).
2. **Ошибки и /tools**: ProblemDetail для 422/404/500, `GET /tools` с `config_schema`.
3. **Контракт**: `load_contract` — inline json/yaml, url (httpx2), `contract_unreachable`,
   `contract_invalid`, подсчёт операций, sha256, фильтр операций.
4. **Создание и статус прогона**: `POST /runs` (sync-проверки по порядку из доки, 202,
   `Location`, idempotency, 429), `GET /runs/{id}`, 404.
5. **Движок**: жизненный цикл, заглушки результатов, артефакты, отмена, таймаут.
6. **result / cancel / artifacts**: 409 `run_not_finished`, 409 `run_already_finished`,
   404 `artifact_not_found`, отдача файла с `media_type`.
7. **Запуск**: `uv run testing-tools`, README, ручной smoke через curl.

## Статус

- [x] 1. Каркас
- [x] 2. Ошибки и /tools
- [x] 3. Контракт
- [x] 4. Создание и статус прогона
- [ ] 5. Движок
- [ ] 6. result / cancel / artifacts
- [ ] 7. Запуск
