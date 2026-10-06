# testing_tools

HTTP-шлюз к black-box инструментам тестирования API. Контракт — [`docs/API.md`](docs/API.md),
модели — [`src/testing_tools/api/schemas.py`](src/testing_tools/api/schemas.py).

Все эндпоинты, валидация и ошибки настоящие. Инструменты по умолчанию тоже настоящие
(`TT_TOOL_MODE=real`): Schemathesis, Microcks и мутационное тестирование тестов агента.
Режим `TT_TOOL_MODE=stub` возвращает правдоподобные заглушки без запуска инструментов
(см. [`docs/work-docs/v0-facade-plan.md`](docs/work-docs/v0-facade-plan.md)). Маршруты — под `/api/v0`.

Как шлюз устроен внутри — [`docs/gateway.md`](docs/gateway.md).

## Запуск

```bash
cp .env.example .env   # по желанию: поправить настройки
uv run testing-tools
```

Swagger — `http://127.0.0.1:8000/docs`.

Настройки читаются из переменных окружения `TT_*`, затем из `.env` в текущем каталоге
(окружение процесса важнее). Шаблон со всеми ключами — [`.env.example`](.env.example).

| env                    | по умолчанию | что делает                                         |
|------------------------|--------------|----------------------------------------------------|
| `TT_HOST` / `TT_PORT`  | `127.0.0.1` / `8000` | адрес сервера                              |
| `TT_API_PREFIX`        | `/api/v0`    | префикс маршрутов                                  |
| `TT_TOOL_MODE`         | `real`       | `real` — запускать инструменты, `stub` — заглушки  |
| `TT_TOOL_DELAY_S`      | `1.0`        | сколько «работает» каждый заглушечный инструмент (только `stub`) |
| `TT_MAX_ACTIVE_RUNS`   | `16`         | лимит активных прогонов, дальше `429`              |
| `TT_UNAVAILABLE_TOOLS` | пусто        | инструменты через запятую, которые считать лежащими (`503`, `degraded`) |
| `TT_WORK_DIR`          | пусто        | где создавать временные каталоги инструментов; пусто — системный temp |
| `TT_KEEP_WORK_DIRS`    | `false`      | не удалять временные каталоги (отладка)            |
| `TT_MICROCKS_URL`      | пусто        | адрес Microcks; пусто — Microcks недоступен (`503`) |
| `TT_MICROCKS_CLIENT_ID` / `TT_MICROCKS_CLIENT_SECRET` | пусто | сервисный аккаунт Keycloak, если в Microcks включена авторизация |
| `TT_MICROCKS_POLL_INTERVAL_S` | `1.0` | период опроса теста в Microcks                     |
| `TT_UV_BIN`            | `uv`         | `uv` для venv мутационных тестов с `requirements`  |

## Инструменты

- **Schemathesis** ставится как зависимость пакета и запускается подпроцессом
  (`python -m schemathesis.cli run`). Ничего настраивать не нужно.
- **Microcks** — отдельный сервер. Для локального запуска хватит uber-образа без авторизации:

  ```bash
  docker run -d --name microcks -p 8585:8080 quay.io/microcks/microcks-uber:latest
  ```

  и `TT_MICROCKS_URL=http://localhost:8585`. Microcks сам ходит в `target.base_url`,
  так что тестируемый сервис должен быть доступен **из контейнера Microcks**
  (для сервиса на хосте — `http://host.docker.internal:<port>`).
- **Мутационный раннер** встроен: поднимает прокси на `127.0.0.1` и гоняет pytest.
  Тесты агента выполняются этим же интерпретатором (в нём есть `pytest` и `httpx`);
  если у набора есть `requirements`, для него создаётся venv через `uv`. Для
  `tests.kind=git` нужен `git`.

Тесты агента — это произвольный код, он выполняется с правами сервиса. Запускайте
шлюз в изолированном контейнере.

## Проверки

```bash
uv run pytest
uv run mypy src tests
```

Тесты раннеров (`tests/test_tool_*.py`) по-настоящему запускают Schemathesis и pytest
против тестового сервиса с заложенными багами (`tests/buggy_petstore.py`) и занимают
около 30 секунд. Microcks в тестах подменён фейковым REST API.
