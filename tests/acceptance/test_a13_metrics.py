"""А-13 (К-1 – К-4): метрики системы на тестовом наборе.

Шаги: прогон на тестовом наборе (п. 5.3 ТЗ), расчёт метрик по 6.2.
Ожидается: значения по таблице 2 ТЗ.

Таблицы 2 и раздела 6.2 ТЗ в репозитории нет, пороги не проверить. Метрики системы
считаются по её собственным тестам, а их нет (А-4). Расчёт метрик в testing_tools для
готового набора проверен в А-9.
"""

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

SYSTEM_METRICS = (
    "contract_coverage",
    "test_suite_size",
    "runnability",
    "mutation_score",
)


@pytest.mark.xfail(
    strict=True,
    reason="у системы нет своих тестов: test_suite_size/runnability/mutation_score не считаются",
)
def test_system_metrics_are_reported(workdir: Path, tmp_path: Path) -> None:
    with demo_service("petstore") as target, tools_service(tmp_path) as tools:
        with scripted_llm(
            tool_call("run_api_checks", tool_name="schemathesis", max_examples=5)
        ) as llm:
            run = generate(
                PETSTORE_YAML,
                target,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools, "--tools-timeout", "300"),
                timeout=400,
            )

    [metrics] = [m["metrics"] for m in run.report["metrics"]["tool_runs"]]
    for name in SYSTEM_METRICS:
        assert metrics[name] is not None, name
