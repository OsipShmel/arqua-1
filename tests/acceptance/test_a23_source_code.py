"""А-23 (Ф-4, Ф-9): генерация по исходному коду своего сервиса.

Шаги: генерация с путём к исходному коду собственного сервиса на Python/FastAPI.
Ожидается: созданы integration-тесты и unit-тесты для внутренних единиц кода; в отчёте —
Line/Branch Coverage и Input Diversity.
"""

import json
from pathlib import Path

import pytest
from conftest import (
    DEMO,
    PETSTORE_YAML,
    cli_env,
    generate,
    generated_suite,
    scripted_llm,
)


def _run(workdir: Path):
    with scripted_llm() as llm:
        run = generate(
            PETSTORE_YAML,
            "http://127.0.0.1:9",
            env=cli_env(llm),
            cwd=workdir,
            extra=("--source-dir", str(DEMO / "petstore")),
        )
    assert run.code == 0, run.stderr
    return run, llm


def test_source_code_becomes_model_context(workdir: Path) -> None:
    run, llm = _run(workdir)

    source = json.loads((run.run_dir / "source-context.json").read_text("utf-8"))
    [app] = [f for f in source["files"] if f["path"] == "app.py"]
    assert len(app["sha256"]) == 64
    assert "create_pet" in json.dumps(source)
    assert "create_pet" in llm.sent_text(), "the model sees the code"
    assert run.report["source"]["files_total"] == 1


@pytest.mark.xfail(
    strict=True, reason="генератора нет: ни unit-, ни integration-тестов"
)
def test_unit_and_integration_tests_are_generated(workdir: Path) -> None:
    run, _ = _run(workdir)

    names = [p.name for p in generated_suite(run)]
    assert any("unit" in n for n in names) and any("integration" in n for n in names)


@pytest.mark.xfail(
    strict=True, reason="Line/Branch Coverage и Input Diversity не считаются"
)
def test_report_has_code_coverage_and_input_diversity(workdir: Path) -> None:
    run, _ = _run(workdir)

    metrics = json.dumps(run.report["metrics"])
    assert (
        "line_coverage" in metrics
        and "branch_coverage" in metrics
        and "input_diversity" in metrics
    )
