# Контекст, tools API и результаты прогона

Эта версия продолжает CLI по `ДД.txt`: исходники как дополнительный контекст,
summary вместо полного контракта в LLM, журнал и единый формат результатов.
Генератор pytest и self-repair остаются отдельным этапом.

## Границы компонентов

```text
CLI: контракт + параметры + необязательные исходники
  -> summary и AST-контекст
  -> worker -> run_qa_agent -> модель
                           -> run_api_checks -> tools API develop
  <- AgentResult: ответ, usage, результаты tools, необязательные файлы
  -> отчёты, manifest, журнал и артефакты
```

CLI не обращается к tools API. Он задаёт разрешённый список и передаёт worker-у
настройки и исходный контракт по stdin. Клиент агента отправляет полный контракт
только детерминированному tools-сервису. В LLM передаются summary, фрагменты исходников
и ограниченные сводки результатов tools.

## Summary v1

`contract-summary.json` содержит `api`, `operations`, используемые `schemas`,
`security_schemes` и `dependencies` из явных response links.
Параметры операции переопределяют параметры пути по `(in, name)`.
Локальные JSON Pointer ссылки на пути, параметры, тела, ответы и примеры разрешаются.
Для схем используется `{"schema_ref":"#/components/schemas/Name"}` с определением
в `schemas` под тем же ключом: это поддерживает рекурсию и исключает повторение схем.
Соседние ограничения `$ref` сохраняются через `allOf`.

Сохраняются поддерживаемые ограничения схем: диапазоны, длины, pattern, enum, const,
multipleOf, nullable, обязательные поля, boolean schemas, композиции, примеры и
явный `default: null`. Неиспользуемые схемы, contact/license и произвольные
vendor-расширения не копируются. Это не полная валидация JSON Schema.
Внешние документы и `$anchor` пока не поддерживаются, ошибка явная.

`--summary-max-chars` по умолчанию 100000 символов компактного JSON. Превышение
останавливает запуск до обращения к модели; операции не удаляются молча.
Бюджет символов не является числом токенов или оценкой стоимости.

## Python/FastAPI-контекст

```bash
uv run --locked arqa-testgen generate openapi.yaml --base-url http://localhost:8080 --source-dir ./service --source-max-chars 24000
```

Сборщик использует `ast.parse`, не импортирует и не выполняет сервис. Сохраняются
imports, имена, строки, декораторы и фрагменты функций/классов. Декораторы показывают
FastAPI endpoints; полноценная межмодульная карта зависимостей не строится.

Ограничения: до 500 подходящих `.py` файлов, один файл до 200 KB, фрагмент до 4000
символов. Пропускаются скрытые каталоги, venv, caches, node_modules, тестовые каталоги,
test-файлы и символические ссылки. Синтаксические ошибки и не-UTF-8 файлы отмечаются
в `skipped`. `--source-max-chars` ограничивает JSON-список отобранных файлов;
метаданные и до 100 причин пропуска учитываются отдельно. Ограничение фрагмента
или отбор по бюджету отмечаются через `truncated`.

Если контекст не найден, явно указанный каталог даёт ошибку. Отсутствие флага
не блокирует обычный запуск. `source-context.json` содержит отобранный код,
manifest — его пути и хеши. Выбирайте исходники, предназначенные для передачи модели:
исключение `.env` не заменяет поиск секретов внутри Python-файлов.
Установка исходников в runner и генерация unit-тестов пока не реализованы.

## Tools API из develop

Новый HTTP-сервис testing_tools разворачивается отдельно из `develop` по
[DEMO.md](https://github.com/OsipShmel/arqua-1/blob/develop/DEMO.md).
Старый пакет testing_tools в `model` не заменяется автоматически.

Из рабочей копии CLI:

```bash
uv run --locked arqa-testgen generate openapi.yaml --base-url http://localhost:8080 --tools-url http://localhost:8000/api/v1 --tools schemathesis microcks --tools-timeout 120
```

По умолчанию при gateway разрешён только Schemathesis. Агент получает
`run_api_checks(tool_name="schemathesis", max_examples=50)`; Microcks должен быть
настроен на сервере. EvoMaster не поддерживается. Разрешение tool не гарантирует,
что модель его вызовет.

Протокол клиента соответствует develop:

1. `POST /api/v1/runs`: inline JSON-контракт, target, выбранный tool, timeout.
2. `GET /api/v1/runs/{id}` до терминального состояния.
3. `GET /api/v1/runs/{id}/result` и `.../artifacts/{name}`.
4. При обычной ошибке/превышении ожидания — попытка `POST .../{id}/cancel`.

`labels.arqa_run_id` связывает ID CLI и tools. Запрос и результат сохраняются в
`tool_artifacts/{id}/request.json` и `result.json`. Имена файлов проверяются, клиент
строит endpoint сам и не следует произвольным URL из ответа. Бюджет скачивания —
10 MB на tools-прогон, обратная связь модели — до 24000 символов; детали остаются
в файлах. `tool_mode: "external"` означает HTTP API, а не проверку серверного
`TT_TOOL_MODE=real`: сервис может быть настроен и на stub.

### Адреса Docker-стенда

Контракт загружает машина CLI, target должен быть доступен из сети tools-сервиса:

```bash
uv run --locked arqa-testgen generate http://localhost:18081/openapi.yaml --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1
```

`localhost:18081` доступен с хоста, `petstore:8080` — из Docker-сети develop.
`DEEPCODE_BASE_URL` указывает отдельно на провайдера модели.

### Tools на Windows

Перед непосредственным запуском testing-tools из его копии develop задайте UTF-8:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
uv run testing-tools
```

Без этого Schemathesis может завершиться при печати Unicode в cp1251/cp1252.
Проблема обнаружена на интеграционной проверке; код develop при этом не менялся.
При жёстком убийстве worker тот не сможет отправить cancel. Удалённый прогон
ограничен своим timeout; его ID остаётся в журнале и каталоге артефактов.

## AgentResult v1

Модель — `agent.results.AgentResult`, контракт —
[agent-result.schema.json](agent-result.schema.json).
Старый ответ `{"status":"success","output":"..."}` принимается с режимом unknown.
Пример интерфейса будущего генератора, а не результат текущей генерации:

```json
{
  "result_version": 1,
  "status": "success",
  "output": "Один тест сохранён; исполнение не выполнялось",
  "tool_mode": "unknown",
  "metrics": null,
  "execution": {"state": "not_run"},
  "files": [{
    "path": "tests/test_example.py",
    "kind": "pytest",
    "encoding": "utf-8",
    "content": "def test_example():\n    assert 1 == 1\n"
  }]
}
```

Файлы передаются содержимым, а не произвольными путями на компьютере пользователя.
Допустимы kind `pytest`, `junit`, `log`, `report`, `other`, encoding UTF-8/base64.
До записи проверяется вся пачка: до 200 файлов, всего до 10 MB, относительные пути,
конфликты регистра/каталогов, Windows-имена и недопустимые символы. Для pytest
требуется `test_*.py`. Файлы создаются в `agent_artifacts/` и CLI их не исполняет.
Индекс содержит пути, размеры и SHA-256. `generated_tests` строится только по
сохранённым pytest-файлам; текст ответа модели не считается подтверждением файла.

`report.status` относится к вызову агента. Код CLI 0 может сопровождаться
`execution.state: "failed"`, если инструмент нашёл дефекты. Состояния execution:
passed, failed, error, unknown, not_run. Полные результаты — `tool_runs`, метрики —
`metrics.tool_runs`, локальные файлы — `tool_artifacts`. Failed означает находки
или падения проверок, error — ошибку выполнения. Классификация ошибок будущего
генератора остаётся задачей генератора и исполнительной среды.

## События, usage и manifest

`events.jsonl`: UTC-время, общий run_id, события run/contract/context/source,
agent, llm, tool и gateway. Записываются начало/завершение/ошибки, время LLM,
usage, имя tool, ограниченный результат и ID удалённого прогона.
Промпты и полный код в журнал не копируются; полные результаты tools лежат отдельно.
Ключ провайдера скрывается в обрабатываемых ошибках, дампа окружения нет.

`runtime.json` обновляется callback-ами и сохраняет частичные сведения при таймауте:

- модель, temperature, timeout, max_retries, версия и SHA-256 промпта;
- `llm_calls`: логические вызовы модели, без отдельного счётчика HTTP-retries SDK;
- `usage_reports`: число ответов с тремя счётчиками токенов;
- input/output/total_tokens: известная сумма или null, если usage не получен;
- `usage_complete`: покрыты ли все вызовы без ошибок;
- `cost`: null, отдельная конфигурация тарифов пока не реализована.

`manifest.json`: контракт/хеш, параметры, пути и хеши исходников, Python/платформа,
версии пакетов, Git revision/dirty, хеши кода агента и uv.lock, runtime и индекс
файлов. В отдельной установке Git/lock могут быть недоступны и равны null.
Это учёт происхождения, а не гарантия детерминированности LLM.
Версии форматов: report v2, manifest v1, summary v1, AgentResult v1.

## Что проверено

Автотесты проходят полный путь с настоящим LangChain и локальными имитаторами
провайдера и tools API: summary/исходники, usage, ID, JUnit, события и различие
между успешным вызовом и упавшими проверками. Настоящие LLM-ключи не нужны.

Вручную проверена неизменённая develop
`ad2d5dce4d0e9b8febf0c8cd84120e84fa847898`: stub-сервис и настоящий Schemathesis 4.32.0
на локальном FastAPI с дефектами. LLM заменяла локальная имитация. Запуск Microcks
и оценка качества настоящего провайдера в эту проверку не входили.
