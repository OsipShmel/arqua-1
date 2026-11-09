"""А-25 (Ф-7): выбор инструментов.

Шаги: запуск с выбором инструментов: EvoMaster (глубокий поиск), Microcks (моки).
Ожидается: по журналу вызваны EvoMaster и Microcks соответственно, без выбора —
Schemathesis; результаты переданы в LLM в компактном виде.

Инструменты запускает testing_tools в режиме stub: здесь проверяется выбор и передача, а
не сами инструменты (они — в А-6, А-8, А-14).
"""

import json
from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    demo_service,
    generate,
    scripted_llm,
    tool_call,
    tools_service,
)


def _run(workdir: Path, tmp_path: Path, *tools_args: str, call: dict):
    with (
        demo_service("petstore") as target,
        tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools,
    ):
        with scripted_llm(tool_call("run_api_checks", **call)) as llm:
            run = generate(
                PETSTORE_YAML,
                target,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools, *tools_args),
            )
    return run, llm


def _started_tools(run) -> list[str]:
    return [
        json.loads(line)["tool"]
        for line in (run.run_dir / "events.jsonl").read_text("utf-8").splitlines()
        if '"gateway.started"' in line
    ]


def test_microcks_is_called_when_chosen(workdir: Path, tmp_path: Path) -> None:
    run, llm = _run(
        workdir, tmp_path, "--tools", "microcks", call={"tool_name": "microcks"}
    )

    assert run.code == 0, run.stderr
    assert _started_tools(run) == ["microcks"]
    assert "microcks" in llm.messages(0)[0]["content"]
    compact = json.loads(llm.tool_results()[0])
    assert compact["tools"][0]["tool"] == "microcks"
    assert set(compact) <= {
        "run_id",
        "state",
        "metrics",
        "tools",
        "error",
        "artifacts_skipped",
        "details_omitted",
    }


def test_schemathesis_without_a_choice(workdir: Path, tmp_path: Path) -> None:
    run, _ = _run(workdir, tmp_path, call={})

    assert _started_tools(run) == ["schemathesis"]


@pytest.mark.xfail(
    strict=True,
    reason="testing_tools в develop не знает evomaster (сервер остался в WiP/testing-tools): 422, прогон агента падает",
)
def test_evomaster_is_called_when_chosen(workdir: Path, tmp_path: Path) -> None:
    run, _ = _run(
        workdir,
        tmp_path,
        "--tools",
        "evomaster",
        "--tools-timeout",
        "300",
        call={"tool_name": "evomaster", "max_time_s": 10},
    )

    assert run.code == 0, run.report["error"]
    assert _started_tools(run) == ["evomaster"]
