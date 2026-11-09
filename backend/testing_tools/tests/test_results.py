"""Run-level output: the metrics block, the Markdown report, the on-disk archive."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from petstore import PETSTORE_JSON
from test_runs_create import RUNS, mutation_tool, run_request
from test_runs_lifecycle import wait_for
from testing_tools.api.schemas import (
    BaselineResult,
    ContractCoverage,
    Finding,
    HttpMethod,
    MicrocksResult,
    MutationResult,
    MutationSummary,
    OperationCoverage,
    OperationRef,
    RunCreateRequest,
    SchemathesisResult,
    Severity,
    ToolName,
    ToolState,
)
from testing_tools.app.archive import masked_request
from testing_tools.app.contract import parse_contract
from testing_tools.app.main import create_app
from testing_tools.app.metrics import run_metrics
from testing_tools.app.settings import Settings

GET_PETS = OperationRef(method=HttpMethod.GET, path="/pets", operation_id="listPets")
GET_PET = OperationRef(method=HttpMethod.GET, path="/pets/{petId}", operation_id="getPet")


def coverage(*items: tuple[OperationRef, list[int]]) -> ContractCoverage:
    """Only by_operation matters to run_metrics; the totals are placeholders."""
    return ContractCoverage(
        operations_total=0,
        operations_tested=0,
        operations_ratio=0,
        status_codes_documented=0,
        status_codes_observed=0,
        status_codes_ratio=0,
        undocumented_status_codes_observed=0,
        by_operation=[
            OperationCoverage(
                operation=op,
                tested=True,
                documented_status_codes=[],
                observed_status_codes=codes,
                requests=1,
            )
            for op, codes in items
        ],
    )


def finding(
    op: OperationRef, severity: Severity, tool: ToolName = ToolName.SCHEMATHESIS
) -> Finding:
    return Finding(
        id=f"{op.path}-{severity}",
        tool=tool,
        check="c",
        severity=severity,
        operation=op,
        title="t",
        message="m",
    )


def test_metrics_union_coverage_across_tools() -> None:
    contract = parse_contract(PETSTORE_JSON)
    schemathesis = SchemathesisResult(
        state=ToolState.FAILED,
        duration_s=2.0,
        coverage=coverage((GET_PETS, [200])),
        findings=[finding(GET_PETS, Severity.HIGH), finding(GET_PETS, Severity.LOW)],
    )
    microcks = MicrocksResult(
        state=ToolState.FAILED,
        coverage=coverage((GET_PET, [404])),
        findings=[finding(GET_PET, Severity.HIGH, ToolName.MICROCKS)],
    )

    metrics = run_metrics(contract, [schemathesis, microcks])

    # petstore: 4 operations, 7 documented responses; covered GET /pets 200, GET /pets/{id} 404
    assert metrics.contract_coverage == 0.5
    assert metrics.status_code_coverage == round(2 / 7, 4)
    assert metrics.faults is not None
    assert metrics.faults.findings_total == 3
    assert metrics.faults.by_severity == {Severity.HIGH: 2, Severity.LOW: 1}
    assert metrics.faults.by_tool == {ToolName.SCHEMATHESIS: 2, ToolName.MICROCKS: 1}
    assert metrics.faults.operations_with_findings == 2
    assert metrics.duration_s == {ToolName.SCHEMATHESIS: 2.0}
    assert metrics.mutation_score is None and metrics.runnability is None


def test_metrics_from_mutation_only() -> None:
    mutation = MutationResult(
        state=ToolState.FAILED,
        baseline=BaselineResult(
            tests_total=4, passed=3, failed=1, errors=0, skipped=0,
            runnability=0.75, duration_s=1.0, tests=[],
        ),  # fmt: skip
        summary=MutationSummary(
            mutants_total=2, killed=1, survived=1, timeout=0, no_coverage=0, errors=0,
            mutation_score=0.5, by_operator={},
        ),  # fmt: skip
    )

    metrics = run_metrics(parse_contract(PETSTORE_JSON), [mutation])

    assert (metrics.test_suite_size, metrics.runnability, metrics.mutation_score) == (4, 0.75, 0.5)
    assert metrics.contract_coverage is None and metrics.faults is None


def test_result_has_metrics_and_report(client: TestClient) -> None:
    body = run_request(tools=[{"tool": "schemathesis"}, mutation_tool({"test_a.py": ""})])
    run_id = client.post(RUNS, json=body).json()["run_id"]
    wait_for(client, run_id, "completed")

    result = client.get(f"{RUNS}/{run_id}/result").json()

    metrics = result["metrics"]
    assert metrics["contract_coverage"] == 1.0
    assert metrics["faults"]["findings_total"] == 0
    assert (metrics["test_suite_size"], metrics["runnability"]) == (1, 1.0)
    assert set(metrics["duration_s"]) == {"schemathesis", "mutation"}
    report = result["report"]
    assert report["name"] == "run-report.md"
    assert report["media_type"] == "text/markdown"
    r = client.get(report["url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    text = r.text
    assert text.startswith(f"# Прогон {run_id}")
    assert "| Contract Coverage (операции) | 100.0% |" in text
    assert "## Тесты агента и мутации" in text


def test_finished_run_is_saved_and_survives_restart(tmp_path: Path) -> None:
    settings = Settings(tool_delay_s=0, results_dir=str(tmp_path / "results"))
    target = {"base_url": "http://petstore:8080", "headers": {"X-Key": "tok-9f2c"}}
    body = run_request(target=target)
    with TestClient(create_app(settings)) as client:
        run_id = client.post(RUNS, json=body).json()["run_id"]
        wait_for(client, run_id, "completed")
        before = client.get(f"{RUNS}/{run_id}/result").json()

    [run_dir] = (tmp_path / "results").iterdir()
    assert run_dir.name.endswith(f"_{run_id}")
    assert run_dir.name[:8].isdigit()  # YYYYmmdd... prefix sorts runs by time
    assert {p.name for p in run_dir.iterdir()} == {
        "result.json", "report.md", "request.json", "artifacts", "artifacts.json",
    }  # fmt: skip
    saved_request = json.loads((run_dir / "request.json").read_text())
    assert saved_request["target"]["headers"] == {"X-Key": "***"}
    assert "tok-9f2c" not in (run_dir / "request.json").read_text()

    with TestClient(create_app(settings)) as fresh:  # a new process: nothing in memory
        assert fresh.get(f"{RUNS}/{run_id}").json()["state"] == "completed"
        assert fresh.get(f"{RUNS}/{run_id}/result").json() == before
        assert fresh.get(before["report"]["url"]).status_code == 200
        artifact = before["tools"][0]["artifacts"][0]["url"]
        assert fresh.get(artifact).status_code == 200
        r = fresh.post(f"{RUNS}/{run_id}/cancel")
        assert r.status_code == 409


def test_old_runs_leave_memory_but_stay_readable(tmp_path: Path) -> None:
    settings = Settings(
        tool_delay_s=0, results_dir=str(tmp_path / "results"), max_runs_in_memory=1
    )
    with TestClient(create_app(settings)) as client:
        ids = []
        for _ in range(3):
            run_id = client.post(RUNS, json=run_request()).json()["run_id"]
            wait_for(client, run_id, "completed")
            ids.append(run_id)
        store = client.app.state.store  # type: ignore[attr-defined]

        assert len(store.all()) <= 2
        for run_id in ids:
            assert client.get(f"{RUNS}/{run_id}/result").status_code == 200


def test_saving_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(Settings(tool_delay_s=0, results_dir=""))) as client:
        run_id = client.post(RUNS, json=run_request()).json()["run_id"]
        wait_for(client, run_id, "completed")

    assert not (tmp_path / "results").exists()


def test_masked_request_hides_every_header_value() -> None:
    request = RunCreateRequest.model_validate(
        {
            "contract": {"kind": "url", "url": "http://c/openapi.json", "headers": {"A": "1"}},
            "target": {"base_url": "http://t", "headers": {"B": "2"}},
            "tools": [{"tool": "microcks", "operations_headers": {"globals": {"C": "3"}}}],
        }
    )

    data: dict[str, Any] = masked_request(request)

    assert data["contract"]["headers"] == {"A": "***"}
    assert data["target"]["headers"] == {"B": "***"}
    assert data["tools"][0]["operations_headers"] == {"globals": {"C": "***"}}
    RunCreateRequest.model_validate(data)  # still a valid request
