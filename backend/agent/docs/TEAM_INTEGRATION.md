# Передача CLI в общий прототип

Ветка `model` объединена с `develop` на коммите `61fbfaf`. Дополнение Сергея
`c265ddf` сохранено. Все пакеты находятся в общем uv workspace:

| Каталог | Устанавливаемый пакет | Назначение |
|---|---|---|
| `backend/agent` | `agent` | LangChain-агент и команда `arqa-testgen` |
| `backend/openapi-parser` | `openapi-parser` | Официальный парсер команды |
| `backend/testing_tools` | `testing-tools` | HTTP-сервис инструментов и runner |

Корневой проект, агент и parser требуют Python 3.13+. Дублирующий
`openapi_parser_project` удалён. `uv.lock` согласован с пакетами. При переносе
ветки в develop сохраняйте этот workspace целиком: старые пути в CI, зависимости
`arqa-openapi-parser` и ручные вставки в `sys.path` больше не нужны.

## Запуск

Из корня репозитория:

```bash
uv sync --locked --all-packages --all-extras
uv run --locked arqa-testgen --help
```

Настройки LLM задаются через `.env` по `backend/agent/.env.example`.
Нужны OpenAI-совместимый chat completions API и поддержка tool calling.
`doctor` проверяет локальные настройки, не выполняя запросы к провайдеру:

```bash
uv run --locked arqa-testgen doctor
```

При работающем Docker поднимите tools, Microcks и демонстрационные API:

```bash
docker compose up --build
```

Затем запустите CLI с хоста. Контракт скачивает CLI, адрес target использует
tools-сервис внутри Docker-сети:

```bash
uv run --locked arqa-testgen generate http://localhost:18081/openapi.yaml --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1 --tools schemathesis --tools-timeout 120 --agent-timeout 300
```

Сервис можно запускать без Docker через `uv run --locked testing-tools`.
Тогда `--base-url` должен быть доступен с той же машины, например
`http://localhost:18081`. На Windows перед запуском tools задайте
`PYTHONUTF8=1` и `PYTHONIOENCODING=utf-8` для вывода Schemathesis.

Без `--tools-url` используются демо-инструменты. CLI задаёт разрешённые tools,
агент самостоятельно принимает решение о вызове. Наличие флага не гарантирует вызов.

## Что исправлено при объединении

- CLI использует официальный parser через workspace, его ошибки приводятся
  к понятной ошибке ввода. Наш summary сохраняет security, ограничения, ссылки
  и бюджет размера; полный контракт в LLM не передаётся.
- Parser сохраняет ссылку на рекурсивном ребре схемы вместо бесконечного раскрытия.
  Это проверено на `Node.child -> Node` через CLI-подготовку контракта.
- `doctor` проверяет пакет `openapi-parser` и Python 3.13+.
- Команды проверки, примеры и CI используют новые пути и Python 3.13/3.14.
- Dockerfile tools копирует метаданные и исходники нового workspace-пакета parser.
- Клиент EvoMaster согласован с лимитом процесса сервера из `WiP/testing-tools`:
  `2 * max_time_s + 120`, плюс 10 секунд на получение результатов.
  Для стандартного поиска 60 секунд используйте `--tools-timeout 300`.

Сервер EvoMaster ещё находится в `WiP/testing-tools` (`4ba39b0`) и не включён
в этот merge develop. Для него запускайте соответствующую серверную версию
с Java/JAR. Пример клиента:

```bash
uv run --locked arqa-testgen generate openapi.yaml --base-url http://petstore:8080 --tools-url http://localhost:8000/api/v1 --tools evomaster --tools-timeout 300 --agent-timeout 600
```

## Проверка нашей части

```bash
uv run --locked --package agent --extra dev python -m pytest backend/agent/tests backend/openapi-parser/tests -q
```

На Windows с недоступным системным pytest-temp создайте новый путь в проекте:

```powershell
New-Item -ItemType Directory -Path .pytest-tmp -Force | Out-Null
$testTemp = Join-Path (Get-Location).Path ('.pytest-tmp/' + [guid]::NewGuid().ToString('N'))
uv run --locked --package agent --extra dev python -m pytest backend/agent/tests backend/openapi-parser/tests -q --basetemp $testTemp
```

После полной установки workspace на Python 3.13 и 3.14 проверено `100 passed`.
В набор входит путь CLI -> официальный parser -> настоящий LangChain-агент ->
настоящий HTTP-сервис testing_tools -> настоящий Schemathesis -> отчёт и JUnit.
LLM заменена локальным сценарным сервером, платные запросы не выполняются.
Тест находит намеренно неверный тип поля ответа. Код CLI 0 означает успешное
обращение к агенту, а `execution.state = failed` в этом случае означает найденный
дефект API. Остальные тесты проверяют ошибки, лимиты, контекст, отчёты и артефакты.

Docker-сборка проверяется отдельным заданием CI. Локальный Docker daemon при
подготовке интеграции был выключен; локально общий HTTP-сценарий проверен без него.
Настоящая LLM, Microcks и живой EvoMaster остаются отдельными приёмочными проверками.

## Следующая работа агентной части

CLI и сохранение файлов готовы к `AgentResult v1`, описанному в
[INTEGRATION.md](INTEGRATION.md) и [JSON Schema](agent-result.schema.json).
Сергей может возвращать файлы через `files` с `kind = pytest` и относительными
путями `tests/test_*.py`; CLI проверяет пути и сохраняет содержимое в `agent_artifacts`.
Текст ответа LLM не считается созданным тестовым файлом.

Генератору остаётся формировать собственные тесты и передавать их в существующий
runner, возвращать JUnit и классификацию результатов. Для этого недостаточно
только перечислить тесты в финальном ответе агента. Unit-тесты, user story и
ограниченный self-repair также требуют реализации в агентном сценарии.
