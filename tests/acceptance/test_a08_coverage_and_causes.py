"""А-8 (Ф-8): покрытие и причины падений.

Шаги: прогон на сервисе с внесённым дефектом (код ответа, не описанный в контракте) и
при остановленном сервисе.
Ожидается: сводка покрытых и непокрытых операций, кодов, параметров; причины падений:
«дефект сервиса», «недоступность стенда или инфраструктуры» — с диагностикой.
"""

from pathlib import Path
from typing import Any

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    demo_service,
    free_port,
    generate,
    scripted_llm,
    tool_call,
    tools_service,
)


def _run(workdir: Path, tmp_path: Path, target: str):
    with tools_service(tmp_path) as tools:
        with scripted_llm(
            tool_call("run_api_checks", tool_name="schemathesis", max_examples=25)
        ) as llm:
            run = generate(
                PETSTORE_YAML,
                target,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools, "--tools-timeout", "300"),
                timeout=400,
            )
    assert run.code == 0, run.stderr
    [result] = run.report["tool_runs"]
    return run, result


@pytest.fixture(scope="module")
def defect(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("a08")
    workdir = tmp / "work"
    workdir.mkdir()
    with demo_service("petstore") as target:
        return _run(workdir, tmp, target)


def _schemathesis(result: dict[str, Any]) -> dict[str, Any]:
    tool: dict[str, Any] = result["tools"][0]
    return tool


def test_undocumented_status_code_is_a_service_defect(defect) -> None:
    run, result = defect

    findings = _schemathesis(result)["findings"]
    undocumented = [f for f in findings if f["check"] == "status_code_conformance"]
    assert undocumented, "DELETE /pets/999 answers 200, not in the contract"
    assert undocumented[0]["response"]["status_code"] not in (204, 404)
    assert undocumented[0]["reproduce"].startswith("curl")
    assert run.report["execution"]["state"] == "failed"


def test_coverage_of_operations_and_codes(defect) -> None:
    _, result = defect

    coverage = _schemathesis(result)["coverage"]
    assert coverage["operations_total"] == 4
    assert coverage["operations_tested"] == 4
    assert coverage["status_codes_documented"] > 0
    assert 0 < coverage["status_codes_observed"] <= coverage["status_codes_documented"]
    assert coverage["undocumented_status_codes_observed"] >= 1
    for op in coverage["by_operation"]:
        assert {
            "operation",
            "tested",
            "documented_status_codes",
            "observed_status_codes",
        } <= op.keys()
    metrics = result["metrics"]
    assert metrics["contract_coverage"] == 1.0
    assert 0 < metrics["status_code_coverage"] <= 1


@pytest.mark.xfail(
    strict=True, reason="покрытия параметров нет: только операции и коды ответов"
)
def test_coverage_of_parameters(defect) -> None:
    _, result = defect

    assert "parameters" in str(_schemathesis(result)["coverage"]["by_operation"][0])


def test_stopped_service_is_infrastructure_failure(
    workdir: Path, tmp_path: Path
) -> None:
    stopped = f"http://127.0.0.1:{free_port()}"

    run, result = _run(workdir, tmp_path, stopped)

    assert result["state"] == "failed"
    assert result["error"]["code"] == "target_unreachable"
    assert stopped in result["error"]["message"], "diagnostics name the address"
    assert _schemathesis(result)["state"] == "skipped"
    assert run.report["execution"]["state"] == "error"


@pytest.mark.xfail(
    strict=True,
    reason="в отчёте CLI нет явной причины падения: run-level error виден только в tool_runs, "
    "execution и report.md его не показывают",
)
def test_cause_is_visible_in_cli_report(workdir: Path, tmp_path: Path) -> None:
    run, _ = _run(workdir, tmp_path, f"http://127.0.0.1:{free_port()}")

    assert "target_unreachable" in str(run.report["execution"])
    assert "недоступ" in (run.run_dir / "report.md").read_text("utf-8").lower()
