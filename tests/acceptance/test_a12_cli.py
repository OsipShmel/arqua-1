"""А-12 (Ф-12, П-1): интерфейс командной строки.

Шаги: вызов --help; запуск со всеми параметрами п. 4.8.1.1 ТЗ.
Ожидается: справка выведена; параметры применены; ход выполнения, сводка метрик, пути к
тестам и отчётам выведены.

Списка п. 4.8.1.1 нет под рукой — проверяются все параметры, которые есть у CLI.
"""

from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    demo_service,
    run_cli,
    scripted_llm,
    tool_call,
    tools_service,
)

OPTIONS = ("--base-url", "--output", "--format", "--contract-timeout", "--agent-timeout", "--max-steps",
           "--source-dir", "--source-max-chars", "--summary-max-chars", "--tools-url", "--tools",
           "--tools-timeout")  # fmt: skip


def test_help(workdir: Path) -> None:
    top = run_cli("--help", env=cli_env(), cwd=workdir)
    sub = run_cli("generate", "--help", env=cli_env(), cwd=workdir)
    version = run_cli("--version", env=cli_env(), cwd=workdir)

    assert top.code == sub.code == version.code == 0
    assert "doctor" in top.stdout and "generate" in top.stdout
    for option in OPTIONS:
        assert option in sub.stdout, option
    assert version.stdout.startswith("arqa-testgen ")


def _full_run(workdir: Path, tmp_path: Path, fmt: str):
    source = workdir / "service"
    source.mkdir(exist_ok=True)
    (source / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    with (
        demo_service("petstore") as target,
        tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools,
    ):
        with scripted_llm(
            tool_call("run_api_checks", tool_name="schemathesis"), final="Итог готов."
        ) as llm:
            run = run_cli(
                "generate", str(PETSTORE_YAML), "--base-url", target, "--output", str(workdir / "out"),
                "--format", fmt, "--contract-timeout", "5", "--agent-timeout", "200", "--max-steps", "12",
                "--source-dir", str(source), "--source-max-chars", "5000", "--summary-max-chars", "50000",
                "--tools-url", tools, "--tools", "schemathesis", "--tools-timeout", "60",
                env=cli_env(llm), cwd=workdir,
            )  # fmt: skip
    assert run.code == 0, run.stderr
    return run


def test_all_parameters_are_applied(workdir: Path, tmp_path: Path) -> None:
    run = _full_run(workdir, tmp_path, "json")

    report = run.report
    assert report["limits"] == {"agent_timeout": 200.0, "max_steps": 12}
    params = report["parameters"]
    assert params["contract_timeout"] == 5.0 and params["tools_timeout"] == 60
    assert params["source_max_chars"] == 5000 and params["summary_max_chars"] == 50000
    assert params["tools"] == ["schemathesis"]
    assert (
        report["source"]["files_total"] == 1 and report["source"]["max_chars"] == 5000
    )
    assert run.run_dir.parent == workdir / "out"
    request = (
        next((run.run_dir / "tool_artifacts").iterdir()) / "request.json"
    ).read_text("utf-8")
    assert '"timeout_s": 60' in request


def test_progress_and_report_paths_are_printed(workdir: Path, tmp_path: Path) -> None:
    run = _full_run(workdir, tmp_path, "text")

    assert "Читаю OpenAPI-контракт..." in run.stderr
    assert "Вызываю агент (tools API)..." in run.stderr
    assert "Итог готов." in run.stdout
    assert "Отчёт (json):" in run.stdout and "Отчёт (markdown):" in run.stdout


@pytest.mark.xfail(
    strict=True, reason="в текстовом выводе нет сводки метрик и путей к тестам"
)
def test_metrics_summary_and_test_paths_are_printed(
    workdir: Path, tmp_path: Path
) -> None:
    run = _full_run(workdir, tmp_path, "text")

    assert "Contract Coverage" in run.stdout
    assert "tests/" in run.stdout
