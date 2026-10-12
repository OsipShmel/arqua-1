# ARQA-1

Прототип генерации тестов по OpenAPI-контрактам. Проект использует uv workspace:
`backend/agent`, `backend/testing_tools` и `openapi_parser_project`.

```bash
uv sync --locked --all-packages --all-extras
uv run --locked arqa-testgen --help
```

CLI и настройка модели описаны в [backend/agent/README.md](backend/agent/README.md).
В этой ветке агент вызывает демонстрационные tools; реальная генерация pytest
и исполнение тестов ещё не подключены. HTTP-фасад tools развивается отдельно.
