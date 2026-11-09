"""А-4 (Ф-4, Ф-8): генерация тестов для демо-сервиса.

Шаги: генерация для демонстрационного сервиса; pytest --collect-only по каталогу тестов;
просмотр кода тестов и отчёта.
Ожидается: для каждой значимой операции — тесты штатного сценария, граничных случаев и
обработки ошибок; файлы собираются pytest, используют фикстуры исполнительной среды; у
каждого теста указаны операция, категория, ожидаемый результат.

Генератора pytest в агенте нет: `run_qa_agent` никогда не возвращает `files`, поэтому
`generated_tests` пуст при любой модели. Тесты описывают ожидаемое поведение.
"""

from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    collect_only,
    demo_service,
    generate,
    generated_suite,
    scripted_llm,
    tool_call,
    tools_service,
)

GAP = "генератора pytest нет: агент не возвращает files, generated_tests пуст"
OPERATIONS = ("listPets", "createPet", "getPet", "deletePet")
CATEGORIES = ("positive", "boundary", "negative")


def _generate(workdir: Path, tmp_path: Path, llm=None):
    with (
        demo_service("petstore") as target,
        tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools,
    ):
        return generate(
            PETSTORE_YAML,
            target,
            env=cli_env(llm),
            cwd=workdir,
            extra=("--tools-url", tools, "--max-steps", "40", "--agent-timeout", "900"),
            timeout=1000,
        )


def _check_suite(run, workdir: Path) -> None:
    assert run.code == 0, run.stderr
    files = generated_suite(run)
    assert files, "no pytest files were generated"
    collected = collect_only(files, workdir)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    code = "\n".join(f.read_text("utf-8") for f in files)
    for operation in OPERATIONS:
        assert operation in code, f"no tests for {operation}"
    for category in CATEGORIES:
        assert category in code, f"no {category} tests"


@pytest.mark.xfail(strict=True, reason=GAP)
def test_suite_is_generated(workdir: Path, tmp_path: Path) -> None:
    with scripted_llm(tool_call("run_api_checks", tool_name="schemathesis")) as llm:
        run = _generate(workdir, tmp_path, llm)

    _check_suite(run, workdir)


@pytest.mark.llm
@pytest.mark.xfail(strict=True, reason=GAP)
def test_suite_is_generated_by_real_model(workdir: Path, tmp_path: Path) -> None:
    _check_suite(_generate(workdir, tmp_path), workdir)
