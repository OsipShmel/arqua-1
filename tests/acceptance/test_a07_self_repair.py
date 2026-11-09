"""А-7 (Ф-7): самокоррекция и инструмент по умолчанию.

Шаги: прогон с лимитом самокоррекции 2 итерации на сервисе с внесённым дефектом;
просмотр журнала и рекомендаций.
Ожидается: исправлены только тесты с ошибками тестового кода, итераций не больше 2; тест,
упавший из-за дефекта, не изменён; Schemathesis вызван по умолчанию; рекомендации по
п. 4.1.7.2 ТЗ.
"""

import json
from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    demo_service,
    generate,
    run_cli,
    scripted_llm,
    tool_call,
    tools_service,
)


def test_schemathesis_is_the_default_tool(workdir: Path, tmp_path: Path) -> None:
    with (
        demo_service("petstore") as target,
        tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools,
    ):
        # The model calls the tool without naming one: the default must be Schemathesis.
        with scripted_llm(tool_call("run_api_checks")) as llm:
            run = generate(
                PETSTORE_YAML,
                target,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools),
            )

    assert run.code == 0, run.stderr
    [offered] = llm.requests[0]["tools"]
    assert (
        offered["function"]["parameters"]["properties"]["tool_name"]["default"]
        == "schemathesis"
    )
    assert "schemathesis" in llm.messages(0)[0]["content"]
    [tools_run] = run.report["tool_runs"]
    assert [t["tool"] for t in tools_run["tools"]] == ["schemathesis"]
    started = [
        json.loads(line)
        for line in (run.run_dir / "events.jsonl").read_text("utf-8").splitlines()
        if '"gateway.started"' in line
    ]
    assert [e["tool"] for e in started] == ["schemathesis"]


def _call_disallowed_tool(workdir: Path, tmp_path: Path):
    with tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools:
        with scripted_llm(tool_call("run_api_checks", tool_name="microcks")) as llm:
            run = generate(
                PETSTORE_YAML,
                "http://127.0.0.1:9",
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools),
            )
    return run, llm


def test_disallowed_tool_never_runs(workdir: Path, tmp_path: Path) -> None:
    run, _ = _call_disallowed_tool(workdir, tmp_path)

    assert run.report["tool_runs"] == [], "microcks was not allowed, nothing may run"
    assert "не разрешён" in run.report["error"]["message"]


@pytest.mark.xfail(
    strict=True,
    reason="ошибка инструмента (не тот tool, неверные аргументы, 4xx от testing_tools) обрывает весь "
    "прогон агента вместо ответа модели, чтобы она исправилась",
)
def test_tool_error_goes_back_to_the_model(workdir: Path, tmp_path: Path) -> None:
    run, llm = _call_disallowed_tool(workdir, tmp_path)

    assert run.code == 0, run.report["error"]
    assert "не разрешён" in llm.tool_results()[0]


@pytest.mark.xfail(
    strict=True,
    reason="самокоррекции нет: нет ни генератора тестов, ни лимита итераций",
)
def test_self_repair_iteration_limit_option(workdir: Path) -> None:
    help_text = run_cli("generate", "--help", env=cli_env(), cwd=workdir).stdout

    assert "--max-repair" in help_text or "самокоррек" in help_text


@pytest.mark.xfail(
    strict=True, reason="рекомендаций в отчёте нет: агент отвечает свободным текстом"
)
def test_report_has_recommendations(workdir: Path) -> None:
    with scripted_llm() as llm:
        run = generate(
            PETSTORE_YAML, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    assert run.report.get("recommendations")
