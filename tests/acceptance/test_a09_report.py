"""А-9 (Ф-9): отчёт прогона.

Шаги: открыть отчёт прогона; фильтр по микросервису, операции, категории, статусу.
Ожидается: результаты сгруппированы; метрики п. 4.1.6.2 ТЗ и сравнительная таблица в
отчёте; фильтры отбирают нужные записи.

Отчёт с метриками строит testing_tools (`run-report.md`); отчёт CLI (`report.md`) кладёт
метрики инструментов JSON-блоком.
"""

import httpx
import pytest
from conftest import (
    PETSTORE_YAML,
    ROOT,
    artifact,
    demo_service,
    inline_contract,
    inline_suite,
    tools_run,
    tools_service,
)

SAMPLE = ROOT / "backend/testing_tools/examples/sample_agent_tests"


@pytest.fixture(scope="module")
def stand(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("a09")
    files = {
        "conftest.py": (SAMPLE / "conftest.py").read_text("utf-8"),
        "petstore/test_pets.py": (SAMPLE / "petstore/test_pets.py").read_text("utf-8"),
    }
    with demo_service("petstore") as target, tools_service(tmp) as tools:
        result = tools_run(
            tools,
            {
                "contract": inline_contract(PETSTORE_YAML),
                "target": {"base_url": target},
                "tools": [
                    {"tool": "schemathesis", "max_examples": 20, "seed": 1},
                    {
                        "tool": "mutation",
                        "tests": inline_suite(files),
                        "max_mutants": 10,
                        "seed": 1,
                    },
                ],
                "labels": {"service": "petstore"},
            },
            timeout=600,
        )
        report = artifact(tools, result, "run-report.md").decode()
        yield result, report, tools


def test_report_has_the_metrics(stand) -> None:
    result, report, _ = stand

    assert result["state"] == "completed"
    for metric in ("Contract Coverage (операции)", "Contract Coverage (коды ответов)", "Найдено проблем",
                   "Test Suite Size", "Runnability", "Mutation Score"):  # fmt: skip
        assert metric in report
    metrics = result["metrics"]
    assert metrics["contract_coverage"] == 1.0
    assert metrics["test_suite_size"] == 8
    assert 0 <= metrics["runnability"] <= 1 and 0 <= metrics["mutation_score"] <= 1


def test_results_are_grouped(stand) -> None:
    _, report, _ = stand

    sections = [line for line in report.splitlines() if line.startswith("## ")]
    assert sections[:3] == ["## Метрики", "## Инструменты", "## Находки"]
    assert "## Покрытие контракта: schemathesis" in sections
    assert "## Тесты агента и мутации" in sections


def test_report_names_the_service(stand) -> None:
    _, report, _ = stand

    assert "service=petstore" in report and "Demo Petstore" in report


@pytest.mark.xfail(
    strict=True,
    reason="сравнительной таблицы метрик по инструментам нет: таблица «Инструменты» — только состояние, время, версия",
)
def test_comparative_table(stand) -> None:
    _, report, _ = stand

    tools_table = report.split("## Инструменты", 1)[1].split("##", 1)[0]
    assert "Coverage" in tools_table and "Находок" in tools_table


@pytest.mark.xfail(
    strict=True,
    reason="фильтров нет: в API нет списка прогонов с отбором по сервису/операции/категории/статусу",
)
def test_filters(stand) -> None:
    _, _, tools = stand

    response = httpx.get(
        f"{tools}/runs", params={"service": "petstore", "status": "failed"}
    )
    assert response.status_code == 200 and response.json()
