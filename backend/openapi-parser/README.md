# openapi-parser

Разбор OpenAPI 3.x контракта из файла, по URL или из готового `dict`. Возвращает
типизированную модель: статистику, операции с параметрами, телами и ответами, схемы
компонентов, серверы и компактное summary для LLM. Используется CLI `arqa-testgen`
(пакет [agent](../agent/README.md)) для базовой проверки контракта и метаданных.

Ставится вместе со всем workspace (`uv sync --locked --all-packages` из корня) или
отдельно: `python -m pip install -e ./backend/openapi-parser`.

## Командная строка

```bash
uv run --locked openapi-parser backend/openapi-parser/examples/pets.yaml --stats
```

```text
Источник: backend/openapi-parser/examples/pets.yaml
Название: Pet API
Версия API: 1.0.0
Спецификация: OpenAPI 3.x (3.0.3)
Операций: 3
Путей: 2
Схем: 2
Серверов: 1
```

| Флаг | Что печатает |
|---|---|
| без флагов | статистику и весь разобранный контракт в JSON |
| `--stats` | только статистику |
| `--summary` | компактное summary для LLM в JSON |

Источник — путь к JSON/YAML-файлу или `http(s)://` URL. При ошибке разбора команда
завершается с сообщением `Ошибка разбора контракта: …` и ненулевым кодом.

## Из Python

```python
from openapi_parser import OpenAPIParser

contract = OpenAPIParser().parse("backend/openapi-parser/examples/pets.yaml")

print(contract.title, contract.api_version, contract.openapi_version)
for op in contract.operations:
    print(op.method, op.path, op.operation_id, list(op.responses))
print(contract.stats.as_text())
summary = contract.summary  # dict для передачи в LLM
```

`parse()` принимает путь (`str` или `Path`), URL или уже загруженный документ (`dict`).

| Поле `OpenAPIContract` | Что внутри |
|---|---|
| `title`, `api_version`, `openapi_version`, `description` | из `info` и `openapi` |
| `operations` | `OperationInfo`: метод, путь, `operation_id`, теги, параметры (с учётом параметров пути), тело запроса, ответы, security |
| `operations[].produces_ids`, `consumes_ids` | эвристические связи: поля `id` / `*_id` в ответах 2xx и в телах запросов |
| `schemas` | схемы из `components.schemas` в виде `SchemaInfo` |
| `servers` | `ServerInfo` из `servers` |
| `stats` | `ContractStats`: тип спецификации, число операций, путей, схем, серверов |
| `summary` | компактное представление для LLM |
| `raw` | исходный документ |

Локальные `$ref` (`#/…`) разрешаются, глубина ограничена 50; на рекурсивной схеме
сохраняется ссылка вместо бесконечного раскрытия.

## Ошибки

`OpenAPIParseError` — документ не OpenAPI 3.x: Swagger 2.0, нет `openapi`, `info` или
`paths`. `OpenAPILoadError` — файл или URL не прочитался или это не JSON/YAML.
Сообщения на русском и описывают причину.

## Ограничения

- Это базовая проверка, а не валидация по спецификации OpenAPI и JSON Schema.
- Внешние `$ref` (на другие файлы и URL) не разрешаются.
- `produces_ids`/`consumes_ids` агент пока не использует: в summary для модели связи
  попадают только из явных OpenAPI `links`.

## Тесты

```bash
uv run --locked python -m pytest backend/openapi-parser/tests
```
