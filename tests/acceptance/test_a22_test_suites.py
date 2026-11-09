"""А-22 (С-2): тесты прототипа локально и в CI.

Шаги: запуск тестов прототипа локально и в CI.
Ожидается: все проверки завершены успешно.

Сами наборы прогоняются отдельно (см. docs/pmi-report.md). Здесь проверяется, что CI
запускает каждый из них.
"""

import yaml
from conftest import ROOT

SUITES = (
    "backend/agent/tests",
    "backend/openapi-parser/tests",
    "backend/testing_tools",
    "tests/acceptance",
)


def _ci_commands() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/cli.yml").read_text())
    return "\n".join(
        str(step.get("run", ""))
        for job in workflow["jobs"].values()
        for step in job["steps"]
    )


def test_ci_runs_every_suite() -> None:
    commands = _ci_commands()

    for suite in SUITES:
        assert suite in commands, f"CI does not run {suite}"


def test_ci_type_checks_testing_tools() -> None:
    assert "mypy" in _ci_commands()
