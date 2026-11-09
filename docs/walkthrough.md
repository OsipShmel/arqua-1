# Ручной показ ARQA-1

Сценарий, чтобы руками показать, что система делает на самом деле. Всё ниже прогнано на
стенде 2026-11-09; выводы — настоящие, сокращены только длинные места. Показ — минут
20–25, из них ожидания около 3 минут.

Что увидят зрители:

1. Демо-сервис с заранее известными дефектами — их можно потрогать curl'ом.
2. Агент сам выбирает инструменты, гоняет их по сервису и находит все четыре дефекта.
3. Что лежит после прогона: отчёты, журнал, сырые результаты инструментов.
4. Как testing_tools оценивает готовый набор тестов: Runnability, Mutation Score.
5. Как система ведёт себя при отказах.

Команды выполняются из корня репозитория.

## 0. Подготовка (заранее)

Нужны Docker Desktop, uv, Python 3.13+.

```bash
uv sync --locked --all-packages --all-extras
docker compose up --build -d
```

Первая сборка — несколько минут. Microcks поднимается ещё секунд 20–30 после старта
контейнера. Готовность:

```bash
curl -s localhost:8000/health
```

```json
{"status":"ok","version":"0.0.1","tools":{"schemathesis":true,"microcks":true,"mutation":true}}
```

Модель — в `.env` в корне (шаблон: `backend/agent/.env.example`):

```dotenv
DEEPCODE_API_KEY=...
DEEPCODE_BASE_URL=https://.../api
DEEPCODE_MODEL=qwen3.8-flash-next
DEEPCODE_TEMPERATURE=0
DEEPCODE_TIMEOUT=120
```

Перед показом сбросьте данные демо-сервисов — инструменты создают и удаляют записи:

```bash
docker compose restart petstore orders
```

## 1. Стенд и эталонные дефекты

Открыть в браузере:

| Адрес | Что это |
|---|---|
| http://localhost:8000/docs | Swagger testing_tools — сервиса, который запускает инструменты |
| http://localhost:8585 | Microcks UI |
| http://localhost:18081/openapi.yaml | контракт демо-Petstore |

В Petstore специально заложены 4 дефекта — расхождения с контрактом
([demo/README.md](../demo/README.md)). Два видно сразу:

```bash
curl -i -X DELETE localhost:18081/pets/999
```

```text
HTTP/1.1 200 OK
{"deleted":false}
```

По контракту — 404 (дефект P4).

```bash
curl -i 'localhost:18081/pets?limit=101'
```

```text
HTTP/1.1 500 Internal Server Error
{"detail":"internal error"}
```

По контракту `limit ≤ 100` и ответ 400 (дефект P1). Ещё два — 500 на пустое имя (P2) и
пропавшее обязательное поле `status` у новых питомцев (P3). Задача системы — найти их
самой.

## 2. Контракт глазами системы

```bash
uv run --locked openapi-parser http://localhost:18081/openapi.yaml --stats
```

```text
Источник: http://localhost:18081/openapi.yaml
Название: Demo Petstore
Версия API: 1.0.0
Спецификация: OpenAPI 3.x (3.0.3)
Операций: 4
Путей: 2
Схем: 3
Серверов: 0
```

`--summary` покажет компактное представление, которое идёт модели вместо полного
контракта.

## 3. Проверка настроек

```bash
uv run --locked arqa-testgen doctor
```

```text
[ok] python: Python 3.13.14; требуется 3.13+
[ok] agent: 0.1.0
[ok] openapi-parser: 0.1.0
…
[ok] model_settings: Обязательные настройки и значения корректны
[warning] tools: Без --tools-url используются демо-tools; доступность внешнего сервиса не проверялась
Проверка локальная; доступность LLM и поддержка tools не проверялись.
```

Ключ не печатается, к модели запросов нет. `warning` — не ошибка.

## 4. Главное: агент тестирует сервис

```bash
uv run --locked arqa-testgen generate http://localhost:18081/openapi.yaml \
  --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1 \
  --tools schemathesis microcks --tools-timeout 300 --agent-timeout 600
```

Два адреса — это нормально: контракт CLI скачивает с хоста (`localhost:18081`), а
тестирует сервис testing_tools изнутри Docker-сети (`petstore:8080`).

Около 30 секунд. Сначала ход выполнения:

```text
Читаю OpenAPI-контракт...
Контракт: Demo Petstore, OpenAPI 3.0.3; операций: 4
Вызываю агент (tools API)...
```

Потом — ответ модели. Он каждый раз сформулирован по-своему, но по сути такой
(сокращено):

```text
## Что реально выполнялось
| Запуск     | Инструмент   | Результат | Объём |
| b131c77c…  | schemathesis | failed    | 4/4 операции, 732 тест-кейса, 10 падений, 2.98 с |
| 197230b3…  | microcks     | failed    | 4/4 операции, 5 шагов, прошёл 1 шаг, 1.31 с |

Покрытие: операции — 100 % (4/4). По статус-кодам: schemathesis наблюдал 8 из 10
задокументированных + 3 незадокументированных.

### 1. Критично: 500 вместо валидации входных данных
- POST /pets с {"name": "", "tag": "cat"} → 500 …
- GET /pets?limit=101 → 500 …
curl -X POST -H 'Content-Type: application/json' -d '{"name": "", "tag": "cat"}' http://petstore:8080/pets

### 2. Высокий: сущность Pet возвращается без обязательного status
### 3. Высокий: DELETE несуществующего ресурса возвращает 200 вместо 404
…
## Что осталось непокрытым
## Рекомендуемые действия
```

В конце:

```text
Режим tools: external. Успешный вызов агента не означает прохождение тестов.
Отчёт (json): …/runs/20261109T100902899283Z-09ac84cc/report.json
Отчёт (markdown): …/report.md
…
```

**Что здесь показать.** Все четыре дефекта найдены, и каждый с командой для
воспроизведения. Инструменты агент выбрал сам: CLI только разрешил два. Модель свела
10 + 4 находки инструментов к четырём причинам.

### Что было внутри

Журнал `events.jsonl` — по нему видно каждый шаг:

```text
10:09:02 run.started
10:09:03 contract.loaded       Demo Petstore
10:09:03 context.prepared      операций 4, summary 3512 символов
10:09:04 llm.started → 10:09:06 llm.finished (2.5 с)
10:09:06 gateway.started       schemathesis
10:09:09 gateway.finished      schemathesis, completed
10:09:09 llm.started → 10:09:13 llm.finished (3.8 с)
10:09:13 gateway.started       microcks
10:09:15 gateway.finished      microcks, completed
10:09:15 llm.started → 10:09:31 llm.finished (16.5 с)  ← итоговый ответ
10:09:31 run.finished          success
```

Три обращения к модели, два прогона инструментов. Сами проверки заняли 5 секунд,
остальное время — модель.

### Что осталось на диске

```text
runs/20261109T100902899283Z-09ac84cc/
  report.md, report.json      отчёт прогона
  manifest.json               версии пакетов, Git, хеши кода, lock-файла и контракта
  events.jsonl                журнал этапов
  runtime.json                модель, параметры, токены
  contract-summary.json       что ушло модели вместо контракта
  tool_artifacts/<id>/        по каталогу на прогон инструмента:
    request.json, result.json   что отправили в testing_tools и что получили
    run-report.md               отчёт testing_tools: метрики, находки, покрытие
    schemathesis-junit.xml, schemathesis.har, …   сырые отчёты Schemathesis
    microcks-test-result.json   сырой результат Microcks
```

`report.md`, начало:

```text
- Статус вызова: success
- Режим tools: external
- Итог проверок: failed
- Контракт: Demo Petstore 1.0.0; операций: 4
LLM-вызовов: 3; сообщений с usage: 3.
Зафиксировано токенов: 18556; полный учёт: True.
```

`success` — агент отработал. `failed` — проверки нашли дефекты. Это два разных итога,
и это так задумано.

`tool_artifacts/<id schemathesis>/run-report.md`:

```text
| Contract Coverage (операции)     | 100.0% |
| Contract Coverage (коды ответов) | 80.0%  |
| Найдено проблем                  | 10     |

| Важность | Инструмент   | Проверка                    | Операция             | Сообщение |
| high     | schemathesis | response_schema_conformance | POST /pets           | "status" is a required property |
| high     | schemathesis | status_code_conformance     | DELETE /pets/{petId} | Received: 200 |
| critical | schemathesis | not_a_server_error          | POST /pets           | Server error |
…
```

Повторный запуск создаст новый каталог; прошлый не меняется.

## 5. Выбор инструмента

```bash
uv run --locked arqa-testgen generate http://localhost:18081/openapi.yaml \
  --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1 \
  --tools microcks --tools-timeout 300
```

Разрешён только Microcks — модель вызывает только его. Проверить по журналу:

```bash
grep gateway.started runs/<каталог>/events.jsonl
```

Без `--tools` разрешён только Schemathesis.

## 6. Оценка набора тестов (testing_tools без агента)

testing_tools — самостоятельный сервис. Скрипт делает то же, что агент: отправляет
задание, опрашивает статус, печатает итог. Набор тестов в
`backend/testing_tools/examples/sample_agent_tests/` написан вручную и играет роль
тестов, которые в будущем будет генерировать агент.

```bash
docker compose restart petstore orders
cd backend/testing_tools/examples && python3 run_demo.py petstore
```

Около 40 секунд, в строке статуса видно, как идут инструменты:

```text
Метрики
  Contract Coverage, операции:     100.0%
  Contract Coverage, коды ответов: 80.0%
  Находок black-box инструментов:  12
  Test Suite Size:                 8
  Runnability:                     50.0%
  Mutation Score:                  100.0%

mutation: failed, 30.0 с
  тест failed: test_pets.py::test_limit_above_maximum_is_rejected  AssertionError: assert 500 == 400
  тест failed: test_pets.py::test_create_pet  KeyError: 'status'
  тест failed: test_pets.py::test_create_pet_with_empty_name_is_rejected  AssertionError: assert 500 == 422
  тест failed: test_pets.py::test_delete_missing_pet  AssertionError: assert 200 == 404
```

**Что здесь показать.**
- Упали ровно 4 теста, и это ровно 4 дефекта. Runnability 50% тут не «плохие тесты», а
  найденные баги.
- Mutation Score 100%: testing_tools портил ответы сервиса (менял типы, удалял поля,
  подменял коды), и тесты заметили каждую порчу.
- Отчёт сохраняется рядом со скриптом (`report-petstore-<id>.md`) и в архив сервиса:

```bash
docker compose exec testing-tools ls /data/results
```

Тот же запрос можно отправить руками из Swagger: http://localhost:8000/docs →
`POST /api/v1/runs`. Формат — [API.md](../backend/testing_tools/docs/API.md).

## 7. Отказы

**Сервис лежит.**

```bash
docker compose stop petstore
uv run --locked arqa-testgen generate demo/petstore/openapi.yaml \
  --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1
docker compose start petstore
```

Код 0, падения нет. Модель получает причину и пишет:

```text
## Итог: тесты не выполнены — сервис недоступен
error.code    = target_unreachable
error.message = http://petstore:8080/: ConnectError('[Errno -2] Name or service not known')
tools[0].tool = schemathesis, state = "skipped"
Schemathesis не стартовал — не было ни одного HTTP-запроса к API.
### Метрики — все пустые, их нельзя интерпретировать как «пройдено»
```

В `report.json`: `execution.state = error`.

**Битый контракт** — модель не вызывается:

```text
Читаю OpenAPI-контракт...
Ошибка: Документ OpenAPI должен содержать объект 'paths'.
```

Код 1, отчёт об ошибке сохранён.

**Логин и пароль в URL** — отказ ещё до запуска:

```text
arqa-testgen generate: error: argument --base-url: Учётные данные в URL не поддерживаются
```

## 8. Сверять выводы модели с фактами

Модель пересказывает результаты инструментов и иногда объясняет их неверно. В
прогоне из раздела 4 она заявила, что Microcks отправил в URL буквальный `{petId}`, и
поэтому получил 404 на `GET /pets/{petId}`. Это не так: сырой результат показывает
подстановку примера `petId=1`, а 404 вышел потому, что Schemathesis отработал раньше и
удалил питомца с id 1. На свежем сервисе этот шаг Microcks проходит.

Факты всегда в `tool_artifacts/<id>/result.json` и сырых отчётах инструментов. Для
показа это хороший момент: система разделяет «что сделали инструменты» (детерминировано,
на диске) и «как это объяснила модель».

И ещё: если testing_tools запущен с `TT_TOOL_MODE=stub`, результаты выдуманы, а модель
об этом не знает и выдаёт их за реальные (BUG-03 в
[known-issues.md](known-issues.md)). На стенде из `docker-compose.yml` режим `real`.

## 9. Что честно сказать

- **Тесты агент пока не генерирует.** Он исследует API готовыми инструментами
  (Schemathesis, Microcks) и пишет отчёт. Набор в разделе 6 — ручной; так будут
  оцениваться сгенерированные тесты, когда появится генератор.
- EvoMaster в CLI есть, в сервисе — нет.
- Полный список: [pmi-report.md](pmi-report.md) (по пунктам ПМИ) и
  [known-issues.md](known-issues.md) (дефекты).

## Без Docker и ключа

Если стенд не поднять, есть локальный показ: настоящие CLI, testing_tools и
Schemathesis, модель заменена сценарием.

```bash
uv run --locked python backend/agent/examples/demo_cli.py
```

Подробности — [MACOS_DEMO.md](../backend/agent/docs/MACOS_DEMO.md).

## После показа

```bash
docker compose down
```

С удалением сохранённых результатов testing_tools — `docker compose down -v`. Каталоги
`runs/` CLI создаёт там, где его запускали; в Git они не попадают.
