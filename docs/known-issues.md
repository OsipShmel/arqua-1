# Известные проблемы

Что сломано или ведёт себя неправильно в компонентах ARQA-1 на `develop`
(`d190bc2`). Найдено при приёмке по ПМИ ([docs/pmi-report.md](pmi-report.md)). Для
большинства пунктов есть тест в `tests/acceptance`, помеченный `xfail(strict=True)`:
когда проблему исправят, он упадёт с XPASS, и маркер нужно снять.

Здесь только дефекты и проблемы интеграции. Чего в системе нет как функции
(генератор тестов, самокоррекция, фильтры отчёта и т. п.) — в отчёте по ПМИ.

Серьёзность: **высокая** — ломает основной сценарий; **средняя** — неверный результат
или вводит в заблуждение; **низкая** — неудобство.

| ID | Компонент | Серьёзность | Коротко |
|---|---|---|---|
| [BUG-01](#bug-01) | agent | высокая | ошибка инструмента или модели обрывает весь прогон агента |
| [BUG-02](#bug-02) | agent ↔ testing_tools | высокая | EvoMaster есть в агенте, но не в сервисе |
| [BUG-03](#bug-03) | testing_tools ↔ agent | средняя | результат заглушки (`TT_TOOL_MODE=stub`) не помечен, модель выдаёт его за реальный |
| [BUG-04](#bug-04) | agent | средняя | маскирование ключа портит отчёты при коротком ключе |
| [BUG-05](#bug-05) | agent | средняя | причина «стенд недоступен» не видна в отчёте CLI |
| [BUG-06](#bug-06) | testing_tools | средняя | API принимает учётные данные в `target.base_url` |
| [BUG-07](#bug-07) | agent ↔ testing_tools | средняя | агент не может отправить тесты в runner (`mutation`) |
| [BUG-08](#bug-08) | agent | низкая | непонятные сообщения: сырой PyYAML, «Connection error.» |
| [BUG-09](#bug-09) | agent ↔ openapi-parser | низкая | связи операций из parser не попадают в summary |
| [BUG-10](#bug-10) | репозиторий | низкая | `pytest backend/testing_tools` из корня даёт 17 ошибок |
| [BUG-11](#bug-11) | testing_tools | низкая | один дефект — много находок, «Найдено проблем» завышено |
| [BUG-12](#bug-12) | demo | низкая | результаты на Orders зависят от состояния сервиса, DEMO.md расходится с прогоном |
| [INF-01](#inf-01) | agent | средняя | у агента/CLI нет Dockerfile и сервиса в compose |

Исправлено в ветке `feat/pmi-acceptance` (в списке нет): разные версии Python в
workspace, корневой `pyproject.toml` без пакетов, CI без тестов testing_tools.

---

## BUG-01

**Ошибка инструмента или модели обрывает весь прогон агента.** Компонент: agent.

Что происходит. Если внутри вызова инструмента возникает исключение, LangChain его не
перехватывает: агент завершается со `status: error`, CLI — с кодом 1. Модель не видит
ошибку и не может исправиться. То же с ошибкой провайдера модели.

Когда случается:
- модель вызвала неразрешённый инструмент (`--tools schemathesis`, а модель зовёт
  `microcks`);
- неверный аргумент (`max_examples=0`, `max_time_s=5`);
- testing_tools ответил 4xx/5xx (например, 422 на `evomaster`, см. BUG-02);
- провайдер вернул ошибку на шаге модели: Ollama отвечает 500 на битый вызов
  инструмента от модели (`XML syntax error … element <function> closed by
  </parameter>`). Встречено на `qwen3.6:35b-a3b` в А-5 — прогон агента оборвался на
  первом шаге.

Воспроизвести: `uv run --locked pytest tests/acceptance/test_a07_self_repair.py -k
tool_error --runxfail`.

Где: `backend/agent/src/agent/gateway.py`, `ToolsGateway.run` бросает `ValueError` /
`httpx.HTTPStatusError`; инструмент `run_api_checks` (`as_tools`) создан без
`handle_tool_error`. Ошибки модели доходят до `worker.py` и становятся ошибкой прогона.

Что сделать: возвращать ошибку инструмента модели текстом (`handle_tool_error=True` или
`try/except` в `run_api_checks`); для ошибок провайдера — ограниченный повтор шага.

## BUG-02

**EvoMaster есть в агенте, но не в сервисе.** Компонент: agent ↔ testing_tools.

Что происходит. CLI принимает `--tools evomaster`, системный промпт предлагает его
модели, gateway отправляет `{"tool": "evomaster"}`. В testing_tools на `develop`
`ToolName` = `schemathesis | microcks | mutation`, и запрос получает `422
validation_error`. Из-за BUG-01 прогон агента падает.

Воспроизвести: `uv run --locked pytest tests/acceptance/test_a25_tool_choice.py -k
evomaster --runxfail`.

Где: `backend/agent/src/agent/gateway.py` (`SUPPORTED_TOOLS`), `cli.py` (`--tools`
choices), `agent.py` (`EXTERNAL_SYSTEM_PROMPT`); `backend/testing_tools/src/testing_tools/api/schemas.py`
(`ToolName`). Сервер EvoMaster лежит в ветке `WiP/testing-tools` (`4ba39b0`) и в
`develop` не влит.

Что сделать: влить EvoMaster в testing_tools или убрать его из агента до этого.

## BUG-03

**Результат заглушки не помечен, модель выдаёт его за реальный.** Компонент:
testing_tools ↔ agent.

Что происходит. В режиме `TT_TOOL_MODE=stub` testing_tools возвращает правдоподобные
результаты без запуска инструментов. В `RunResult` это никак не отмечено: состояние
`passed`, версия инструмента, покрытие — как у настоящего прогона. Агент передаёт это
модели, и модель пишет пользователю, например: «Найдено дефектов: 0. Охват кодов ответа
100%». Так ответила `qwen3.6:35b-a3b` на демо-Petstore, где на самом деле 4 дефекта.
`tool_mode: external` в отчёте CLI режим сервера тоже не показывает.

Где: `backend/testing_tools/src/testing_tools/app/tools/stub.py`; схема `RunResult` /
`ToolResultBase` в `api/schemas.py`; `backend/agent/src/agent/gateway.py` (`compact`).

Что сделать: добавить в результат признак режима (например, `ToolResultBase.simulated:
bool` или `RunResult.tool_mode`), передавать его в сводку для модели и в отчёт CLI.

## BUG-04

**Маскирование ключа портит отчёты при коротком ключе.** Компонент: agent.

Что происходит. Значение `DEEPCODE_API_KEY` вырезается из *любой* строки отчёта, журнала
и ответа агента. У локальных моделей ключ фиктивный и часто короткий (`local`,
`ollama`): тогда `local-llama` в отчёте становится `***-llama`, а
`http://localhost:11434/v1` — `http://***host:11434/v1`. Чем короче ключ, тем больше
искажений.

Воспроизвести: `uv run --locked pytest tests/acceptance/test_a24_local_model.py -k dummy
--runxfail`.

Где: `backend/agent/src/agent/events.py`, `redact` — `value.replace(key, "***")` для
каждой строки; то же в `worker.py` и `cli.py` (`error_message`).

Что сделать: маскировать только значения полей-секретов и ключ длиной от ~16 символов;
либо не маскировать ключ, если он не похож на секрет.

Обход: задавать ключ локальной модели длинной случайной строкой.

## BUG-05

**Причина «стенд недоступен» не видна в отчёте CLI.** Компонент: agent.

Что происходит. При остановленном сервисе testing_tools завершает прогон с `state:
failed` и `error.code: target_unreachable`. В `report.json` в `execution.runs[]` есть
только `state` и инструменты (`skipped`), без run-level `error`; `report.md` результаты
инструментов не показывает. Пользователь видит `execution.state: error` без причины;
она есть только в сыром `tool_runs[].error`.

Воспроизвести: `uv run --locked pytest tests/acceptance/test_a08_coverage_and_causes.py
-k cause_is_visible --runxfail`.

Где: `backend/agent/src/agent/gateway.py`, `outcome()` — `runs` собирается без
`r.get("error")`; `backend/agent/src/agent/cli.py`, `write_reports` — нет раздела о
результатах инструментов.

Что сделать: добавить `error` в `execution.runs[]` и короткий раздел по прогонам
инструментов (состояние, причина, находки) в `report.md`.

## BUG-06

**API testing_tools принимает учётные данные в `target.base_url`.** Компонент:
testing_tools.

Что происходит. `POST /api/v1/runs` с `target.base_url =
"http://user:pass@host"` получает 202, логин и пароль уходят в инструменты и в
сохранённый `request.json`. CLI агента такие URL отклоняет, а прямой вызов API — нет.
ПМИ Б-1 требует отказа.

Воспроизвести: `uv run --locked pytest tests/acceptance/test_a19_secrets.py -k
tools_api --runxfail`.

Где: `backend/testing_tools/src/testing_tools/api/schemas.py`, `Target.base_url:
AnyHttpUrl` без проверки `username/password`.

Что сделать: валидатор на `Target.base_url` (и `UrlContractSource.url`), который
отклоняет userinfo; авторизацию передавать через `headers`.

## BUG-07

**Агент не может отправить свои тесты в runner.** Компонент: agent ↔ testing_tools.

Что происходит. Runner pytest-наборов и мутационное тестирование работают в
testing_tools (`tool: mutation`), но gateway агента умеет только
schemathesis/microcks/evomaster. Даже когда появится генератор, Runnability и Mutation
Score собственных тестов системы не посчитать.

Где: `backend/agent/src/agent/gateway.py` (`SUPPORTED_TOOLS`, формирование `config`).

Что сделать: добавить отправку `{"tool": "mutation", "tests": {"kind": "inline",
"files": …}}` с файлами из `AgentResult.files`.

## BUG-08

**Непонятные сообщения об ошибках.** Компонент: agent.

- Синтаксическая ошибка YAML в контракте: выводится сырой текст PyYAML
  (`while parsing a flow mapping … expected ',' or '}'`), без слов «YAML» и «контракт».
  Тест: `test_a02_contract_errors.py -k yaml_syntax`.
- Недоступный адрес LLM: сообщение `Connection error.` — не сказано, что это модель и
  какой адрес. Тип ошибки виден только в `report.json`. Тест: `test_a16_failures.py -k
  message_is_clear`.

Где: `backend/agent/src/agent/contracts.py` (`read_contract`, `yaml.load`),
`backend/agent/src/agent/worker.py` (общий `except Exception`).

## BUG-09

**Связи операций из parser не попадают в summary.** Компонент: agent ↔ openapi-parser.

Что происходит. `openapi-parser` эвристически находит связи по id
(`OperationInfo.produces_ids` / `consumes_ids`), но summary для модели строит агент
сам и берёт связи только из явных OpenAPI `links`. В контрактах без `links` (таких
большинство, в том числе демо) связей «создать → прочитать» в контексте модели нет.
Кроме того, эвристика parser смотрит только на тела, а не на параметры пути
(`/pets/{petId}`), поэтому и сама связь неполная.

Тест: `test_a03_context.py -k implicit`.

Где: `backend/agent/src/agent/summary.py` (`dependencies` — только из `links`);
`backend/openapi-parser/src/openapi_parser/parser.py` (`_infer_id_links`).

## BUG-10

**`pytest backend/testing_tools` из корня даёт 17 ошибок.** Компонент: репозиторий.

Что происходит. Если указать каталог пакета, pytest собирает и
`examples/sample_agent_tests` — примеры тестов «агента», которым нужен живой сервис и
`ARQA_BASE_URL`. `testpaths = ["tests"]` действует только при запуске без аргументов.

Обход: `pytest backend/testing_tools/tests` (так в README и CI).

Что сделать: `norecursedirs = ["examples"]` в `[tool.pytest.ini_options]` пакета.

## BUG-11

**Один дефект — много находок.** Компонент: testing_tools.

Что происходит. Находки Schemathesis дедуплицируются по конкретному запросу, а не по
причине. На демо-Petstore дефект P3 (нет поля `status`) даёт 6 находок
`response_schema_conformance`: по две на каждую из трёх операций, с телом с `tag` и
без. Метрика «Найдено проблем» и `faults.findings_total` показывают 11 при 4 реальных
дефектах. Модели уходят первые 10 находок, и одна причина может вытеснить остальные.

Где: `backend/testing_tools/src/testing_tools/app/tools/schemathesis.py` (сбор
находок), `app/metrics.py` (`_faults`).

Что сделать: группировать по `(check, operation, status_code, первая строка message)`
и считать группы; конкретные запросы оставить внутри группы как примеры.

## BUG-12

**Результаты на Orders зависят от состояния сервиса.** Компонент: demo.

Что происходит. Демо-сервисы хранят данные в памяти, инструменты их меняют. Дефект O3
(500 на повторную отмену заказа) проявляется, только если заказ уже отменён, поэтому
Microcks на Orders находит то 0/4 (как в DEMO.md), то 1/4: на нашем стенде — 1/4 после
прогона Schemathesis. FDR по Orders невоспроизводим без перезапуска сервиса.

Обход: `docker compose restart petstore orders` перед каждым замером (DEMO.md об этом
предупреждает).

## INF-01

**У агента/CLI нет Dockerfile и сервиса в compose.** Компонент: agent.

Что происходит. В Docker есть testing_tools, Microcks и демо-сервисы, а CLI
`arqa-testgen` запускается только на хосте. Отсюда:
- сквозной сценарий нельзя поднять одной командой, нужен uv и Python на хосте;
- CLI и testing_tools видят сервис по разным адресам (`localhost:18081` для контракта,
  `petstore:8080` для target): это легко перепутать;
- проверить установку CLI на «чистом» Linux можно только в CI.

Обход для тестов: `tests/docker/` — образ с workspace из `uv.lock` для прогона наборов и
стендовых тестов в сети compose.
