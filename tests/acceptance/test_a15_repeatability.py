"""А-15 (К-6): повторяемость.

Шаги: три повторных прогона А-13 с теми же параметрами.
Ожидается: отклонение R, CCо, CCк не более 10 п. п.

А-13 не выполняется (нет генерации). Повторяемость инструментов при фиксированном seed
проверяется здесь: она нужна, чтобы разброс метрик системы зависел только от модели.
"""

from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    ROOT,
    demo_app,
    inline_contract,
    inline_suite,
    serve,
    tools_run,
    tools_service,
)

SAMPLE = ROOT / "backend/testing_tools/examples/sample_agent_tests"


def _metrics(tmp_path: Path) -> dict:
    files = {
        "conftest.py": (SAMPLE / "conftest.py").read_text("utf-8"),
        "petstore/test_pets.py": (SAMPLE / "petstore/test_pets.py").read_text("utf-8"),
    }
    # A fresh service per run: Schemathesis creates and deletes data.
    with serve(demo_app("petstore")) as target, tools_service(tmp_path) as tools:
        result = tools_run(
            tools,
            {
                "contract": inline_contract(PETSTORE_YAML),
                "target": {"base_url": target},
                "tools": [
                    {"tool": "schemathesis", "max_examples": 20, "seed": 7},
                    {
                        "tool": "mutation",
                        "tests": inline_suite(files),
                        "max_mutants": 10,
                        "seed": 7,
                    },
                ],
            },
            timeout=600,
        )
    return result["metrics"]


def test_tool_metrics_repeat_with_a_fixed_seed(tmp_path: Path) -> None:
    runs = [_metrics(tmp_path / str(i)) for i in range(3)]

    for name in (
        "runnability",
        "contract_coverage",
        "status_code_coverage",
        "mutation_score",
    ):
        values = [r[name] for r in runs]
        assert max(values) - min(values) <= 0.10, (name, values)


@pytest.mark.xfail(strict=True, reason="зависит от А-13: метрик системы нет")
def test_system_metrics_repeat() -> None:
    from agent.gateway import SUPPORTED_TOOLS

    assert "mutation" in SUPPORTED_TOOLS, "the system cannot measure its own suite"
