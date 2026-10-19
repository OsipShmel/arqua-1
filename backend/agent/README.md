# CLI ARQA-1

Полное руководство по проекту, архитектуре, проверкам и демонстрации находится
в [корневом README](../../README.md). Этот файл описывает пакет `agent` и его CLI.

CLI принимает OpenAPI-контракт из локального JSON/YAML-файла или HTTP(S) URL,
готовит summary по операциям, опционально добавляет Python/FastAPI-контекст и сохраняет
результат агента, отчёты, manifest и журнал. Полный контракт в LLM не передаётся.
Агент самостоятельно выбирает и вызывает tools; CLI к tools-сервису не обращается.

**Текущее состояние:** без `--tools-url` используются демо-tools. При подключении
отдельно запущенного HTTP-сервиса из `develop` агент вызывает Schemathesis/Microcks.
Статус `success` относится к вызову агента; результаты проверок — в `execution`.
Генератора pytest/self-repair ещё нет; CLI готов сохранять возвращённые генератором файлы.
Подробный интерфейс: [INTEGRATION.md](docs/INTEGRATION.md),
машиночитаемый контракт: [AgentResult](docs/agent-result.schema.json).

## Установка

Из корня репозитория, с Python 3.14 и uv:

```bash
uv sync --locked --all-packages --all-extras
uv run --locked arqa-testgen --help
```

Пакет агента допускает Python 3.11+, но корневой workspace требует 3.14.
Parser теперь является отдельным пакетом workspace и устанавливается автоматически.
Для установки без uv в своём виртуальном окружении:

```bash
python -m pip install -e ./openapi_parser_project -e ./backend/agent
```

## Интерфейс агентной части

CLI передаёт задание в `runner.invoke_agent`, который запускает отдельный процесс
`python -m agent.worker`. Worker вызывает функцию агентной части:

```python
run_qa_agent(task: str, *, max_steps: int = 25, telemetry=None, gateway=None) -> dict
```

Она создаёт LangChain-агент и запускает цикл с `recursion_limit=max_steps`.
Успешный результат содержит `status: "success"`, `output` с содержимым последнего
сообщения модели, runtime и режим tools. Опциональные telemetry/gateway передаёт worker.
Исключения функции перехватывает worker,
возвращая CLI `status: "error"`, `error` и `error_type`.

Функция управляет моделью и tools; CLI управляет вводом, временем запуска и файлами
отчётов. Прямой вызов `run_qa_agent` из Python не получает таймаут отдельного процесса:
он реализован в runner. Для генератора используется AgentResult v1 с необязательным
списком files; произвольные пути из текста LLM не загружаются.

## Настройки модели

Скопируйте `backend/agent/.env.example` в `.env` в текущем каталоге или экспортируйте:

```dotenv
DEEPCODE_API_KEY=your_api_key_here
DEEPCODE_BASE_URL=https://your-provider.example/v1
DEEPCODE_MODEL=your-model
DEEPCODE_TEMPERATURE=0
DEEPCODE_TIMEOUT=60
```

Первые три переменные обязательны. Секреты не передаются CLI-флагами.
Переменные процесса имеют приоритет над `.env`. Модель сначала ищет `.env` в дереве
установленного пакета, затем читает `.env` из текущего рабочего каталога без
перезаписи уже заданных значений. Пользовательский `SSL_CERT_FILE` сохраняется.

`DEEPCODE_TIMEOUT` — таймаут одного запроса к LLM, а не всего агентного цикла.
При сетевой ошибке модель допускает одну повторную попытку. Для вызова tools
провайдер и модель должны поддерживать OpenAI-compatible tool calling.
`DEEPCODE_BASE_URL` должен быть HTTP(S) URL без встроенных учётных данных.

## Проверка окружения

```bash
uv run --locked arqa-testgen doctor
uv run --locked arqa-testgen doctor --format json
```

`doctor` проверяет версию Python, установленные зависимости, обязательные настройки,
температуру, таймаут запроса, URL провайдера и наличие файла `SSL_CERT_FILE`, если он задан.
Ключи и значения секретов не выводятся. Команда не создаёт LLM-клиент, не делает сетевых
запросов и не подтверждает доступность провайдера, действительность ключа или поддержку
tool calling. Предупреждение о демонстрационных tools не является ошибкой конфигурации.
Код завершения: `0` — локальные настройки корректны; `1` — обнаружена ошибка.

## Запуск

```bash
uv run --locked arqa-testgen generate openapi.yaml --base-url http://localhost:8080
uv run --locked arqa-testgen generate http://localhost:8080/openapi.json --base-url http://localhost:8080 --output ./runs --format json
uv run --locked arqa-testgen generate openapi.yaml --base-url http://localhost:8080 --agent-timeout 120 --max-steps 15
uv run --locked arqa-testgen generate openapi.yaml --base-url http://localhost:8080 --source-dir ./service --tools-url http://localhost:8000/api/v1 --tools schemathesis
```

Для путей с пробелами используйте кавычки. Альтернативный запуск установленного
пакета: `python -m agent generate ...`.

| Параметр | Назначение |
| --- | --- |
| `contract` | Путь или HTTP(S) URL OpenAPI 3.x JSON/YAML |
| `--base-url` | Обязательный адрес тестируемого сервиса |
| `--output` | Каталог прогонов, по умолчанию `runs` |
| `--format text/json` | Формат stdout, по умолчанию `text` |
| `--contract-timeout` | Положительный таймаут загрузки контракта, по умолчанию 10 секунд |
| `--agent-timeout` | Максимальное время процесса агента, по умолчанию 300 секунд |
| `--max-steps` | Предел шагов графа LangGraph, по умолчанию 25 |
| `--source-dir` | Дополнительный каталог Python/FastAPI-исходников |
| `--source-max-chars` | Бюджет JSON-списка исходных фрагментов, по умолчанию 24000 |
| `--summary-max-chars` | Предел summary в символах, по умолчанию 100000 |
| `--tools-url` | Базовый URL tools API с `/api/v1`; клиент работает в агенте |
| `--tools` | Разрешённые schemathesis/microcks; без списка при gateway — schemathesis |
| `--tools-timeout` | Срок удалённого прогона, по умолчанию 120 секунд |
| `--help`, `--version` | Справка и версия, работают без ключей модели |

CLI использует командный parser для базовой проверки и метаданных, затем отдельный
нормализатор строит summary с операциями, используемыми схемами, ограничениями,
примерами и авторизацией. Локальные JSON Pointer ссылки разрешаются; рекурсивные
схемы сохраняются в реестре. Неиспользуемые данные и произвольные vendor-расширения
в LLM не копируются. Внешние документы и anchors пока не поддерживаются.
URL загружается на машине CLI; требуется хотя бы одна HTTP-операция.
Подготовка summary не является полной валидацией OpenAPI/JSON Schema.

Этапы и диагностика выводятся в stderr. В режиме `--format json` stdout содержит
ровно один JSON-документ в UTF-8, включая перенаправление в файл на Windows.
Вывод самого агента также направляется в stderr.

CLI запускает агент в отдельном процессе, передавая задание через UTF-8 stdin.
Срок `--agent-timeout` включает создание модели и весь агентный цикл, включая повторы
запросов и вызовы tools; загрузка контракта выполняется до него. При превышении срока
или Ctrl+C CLI завершает локальный процесс агента и сохраняет отчёт. `--max-steps`
передаётся агенту как `recursion_limit`: это шаги графа, а не точное число LLM-запросов
или tools. При превышении лимита возвращается `GraphRecursionError`.

## Результаты

Каждый запуск получает каталог с UTC-временем и уникальным ID:

```text
runs/<UTC-time>-<ID>/
  report.json
  report.md
  manifest.json
  events.jsonl
  contract-summary.json
  runtime.json                 # При наличии телеметрии worker
  source-context.json          # При --source-dir
  agent_artifacts/             # Файлы из AgentResult
  tool_artifacts/<tools-id>/   # Запрос, результат и файлы tools API
```

JSON содержит статус вызова, времена, SHA-256 исходного контракта, количество
операций, адрес сервиса, ответ агента, ошибку, применённые лимиты и пути отчётов. Отчёты сохраняются
также при ошибке чтения/модели или Ctrl+C, если каталог доступен для записи.
Прошлые прогоны не перезаписываются. Каждый файл записывается атомарно; при сбое
диска может сохраниться только часть пары, CLI завершится с ошибкой.

Коды завершения: `0` — вызов агента завершён успешно; `1` — ошибка контракта,
агента или сохранения; `2` — неверные/отсутствующие CLI-аргументы;
`124` — истёк срок агента (`status: "timeout"`); `130` — Ctrl+C.
Завершение процесса не отменяет уже начатые операции на удалённом сервисе или
отдельные процессы будущих tools. Протокол отмены таких операций потребует поддержки
со стороны tools.

## Проверки

```bash
uv run --locked --package agent pytest backend/agent/tests openapi_parser_project/tests -q
uv run --locked --package agent ruff check backend/agent/src backend/agent/tests
uv run --locked --package agent ruff format --check backend/agent/src backend/agent/tests
```

Если Windows сообщает `PermissionError` для `%TEMP%\pytest-of-<user>`, используйте
новый временный каталог для каждого запуска. Это обходит недоступный общий каталог
pytest без изменения его прав или удаления чужих файлов. В PowerShell из корня проекта:

```powershell
New-Item -ItemType Directory -Path .pytest-tmp -Force | Out-Null
$testTemp = Join-Path (Get-Location).Path ('.pytest-tmp/' + [guid]::NewGuid().ToString('N'))
uv run --locked --package agent pytest backend/agent/tests openapi_parser_project/tests -q --basetemp $testTemp
```

Если uv задан переменной с путём к исполняемому файлу, замените `uv` на `& $uv`.
Временные файлы остаются в `.pytest-tmp/` для диагностики и исключены из Git.
Не указывайте в `--basetemp` каталог с нужными файлами: pytest очищает его перед запуском.

Интеграционные тесты запускают установленный CLI, настоящий LangChain-цикл и
OpenAI-клиент против локального сценарного HTTP-сервера. Проверяется выбор tool,
передача его результата обратно модели, контракт из файла/URL, JSON и артефакты.
Настоящие ключи, внешняя LLM и платные запросы не используются.

Также проверяются зависший провайдер, лимит шагов, ошибки авторизации без утечки
ключа, локальная диагностика `doctor` и сохранность OpenAPI-контекста.

Workflow `.github/workflows/cli.yml` запускает тесты, lint и проверку форматирования
на Windows и Linux с Python 3.11 и 3.14. Для 3.14 используется workspace с `uv.lock`;
для 3.11 отдельно устанавливаются пакеты CLI и parser, поскольку корневой workspace
и пакет testing-tools требуют 3.14. LLM-ключи в CI не нужны.
