# testing_tools

HTTP-шлюз к black-box инструментам тестирования API. Контракт — [`docs/API.md`](docs/API.md),
модели — [`src/testing_tools/api/schemas.py`](src/testing_tools/api/schemas.py).

**v0**: все эндпоинты, валидация и ошибки настоящие, инструменты — заглушки
(см. [`docs/work-docs/v0-facade-plan.md`](docs/work-docs/v0-facade-plan.md)). Маршруты — под `/api/v0`.

Как шлюз устроен внутри — [`docs/gateway.md`](docs/gateway.md).

## Запуск

```bash
uv run testing-tools
```

Swagger — `http://127.0.0.1:8000/docs`.

| env                    | по умолчанию | что делает                                         |
|------------------------|--------------|----------------------------------------------------|
| `TT_HOST` / `TT_PORT`  | `127.0.0.1` / `8000` | адрес сервера                              |
| `TT_API_PREFIX`        | `/api/v0`    | префикс маршрутов                                  |
| `TT_TOOL_DELAY_S`      | `1.0`        | сколько «работает» каждый заглушечный инструмент   |
| `TT_MAX_ACTIVE_RUNS`   | `16`         | лимит активных прогонов, дальше `429`              |
| `TT_UNAVAILABLE_TOOLS` | пусто        | инструменты через запятую, которые считать лежащими (`503`, `degraded`) |

## Проверки

```bash
uv run pytest
uv run mypy src tests
```
