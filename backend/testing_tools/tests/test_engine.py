import asyncio
import json
from typing import Any

import pytest

from petstore import PETSTORE_JSON, inline_contract
from testing_tools.api.schemas import (
    ErrorCode,
    MicrocksResult,
    MutationResult,
    RunCreateRequest,
    RunState,
    SchemathesisResult,
    ToolState,
)
from testing_tools.app.contract import parse_contract
from testing_tools.app.engine import StubEngine
from testing_tools.app.settings import Settings
from testing_tools.app.store import RunRecord

pytestmark = pytest.mark.anyio

PREFIX = "/api/v0"


def make_record(*tools: dict[str, Any]) -> RunRecord:
    request = RunCreateRequest.model_validate(
        {
            "contract": inline_contract(),
            "target": {"base_url": "http://petstore:8080"},
            "tools": list(tools),
        }
    )
    return RunRecord.new(request, parse_contract(PETSTORE_JSON), PREFIX)


async def run_fast(record: RunRecord) -> None:
    await StubEngine(Settings(tool_delay_s=0)).run(record, timeout_s=10)


async def test_run_completes_with_results_in_request_order() -> None:
    record = make_record({"tool": "microcks"}, {"tool": "schemathesis"})

    await run_fast(record)

    assert record.state == RunState.COMPLETED
    assert record.started_at is not None and record.finished_at is not None
    assert [t.state for t in record.tools] == [ToolState.PASSED, ToolState.PASSED]
    assert all(t.progress == 1.0 for t in record.tools)
    assert isinstance(record.tools[0].result, MicrocksResult)
    assert isinstance(record.tools[1].result, SchemathesisResult)
    assert sorted(record.artifacts) == ["microcks-stub.json", "schemathesis-stub.json"]


async def test_schemathesis_stub_coverage_follows_contract_and_filter() -> None:
    record = make_record(
        {"tool": "schemathesis", "seed": 42, "operations": {"exclude": {"methods": ["DELETE"]}}}
    )

    await run_fast(record)

    result = record.tools[0].result
    assert isinstance(result, SchemathesisResult)
    assert result.seed == 42
    assert result.findings == []
    assert result.summary is not None
    assert result.summary.operations_selected == 3
    assert result.summary.failures == 0
    coverage = result.coverage
    assert coverage is not None
    assert (coverage.operations_total, coverage.operations_tested) == (4, 3)
    assert coverage.operations_ratio == 0.75
    # documented: listPets 200+default, createPet 201+422, getPet 200+404, deletePet 204
    assert (coverage.status_codes_documented, coverage.status_codes_observed) == (7, 5)
    assert coverage.status_codes_ratio == pytest.approx(5 / 7)
    delete_pet = coverage.by_operation[3]
    assert delete_pet.tested is False
    assert delete_pet.observed_status_codes == []
    assert coverage.by_operation[0].observed_status_codes == [200]


async def test_microcks_stub_respects_filtered_operations() -> None:
    record = make_record({"tool": "microcks", "filtered_operations": ["GET /pets"]})

    await run_fast(record)

    result = record.tools[0].result
    assert isinstance(result, MicrocksResult)
    assert result.service_ref == "Petstore API:1.0.0"
    assert [op.microcks_operation for op in result.operations] == ["GET /pets"]
    assert result.summary is not None
    assert (result.summary.operations_total, result.summary.operations_passed) == (1, 1)


async def test_mutation_stub_baseline_counts_test_files() -> None:
    files = {"tests/conftest.py": "", "tests/test_pets.py": "", "tests/test_store.py": ""}
    record = make_record(
        {
            "tool": "mutation",
            "tests": {"kind": "inline", "files": files},
            "operators": ["change_field_type"],
        }
    )

    await run_fast(record)

    result = record.tools[0].result
    assert isinstance(result, MutationResult)
    assert result.state == ToolState.PASSED
    assert result.baseline is not None
    assert result.baseline.tests_total == 2
    assert result.baseline.runnability == 1.0
    assert [t.node_id for t in result.baseline.tests] == [
        "tests/test_pets.py::test_stub",
        "tests/test_store.py::test_stub",
    ]
    assert result.summary is not None
    assert result.summary.mutants_total == 0
    assert result.summary.mutation_score == 0.0
    assert list(result.summary.by_operator) == ["change_field_type"]


async def test_mutation_stub_with_git_suite_has_no_runnable_tests() -> None:
    git_suite = {"kind": "git", "repo_url": "https://x/r.git"}
    record = make_record({"tool": "mutation", "tests": git_suite})

    await run_fast(record)

    result = record.tools[0].result
    assert isinstance(result, MutationResult)
    assert result.baseline is not None and result.baseline.runnability == 0.0
    assert result.state == ToolState.FAILED


async def test_artifact_holds_tool_result() -> None:
    record = make_record({"tool": "schemathesis"})

    await run_fast(record)

    result = record.tools[0].result
    assert result is not None
    artifact = record.artifacts["schemathesis-stub.json"]
    assert artifact.media_type == "application/json"
    assert json.loads(artifact.content)["tool"] == "schemathesis"
    [ref] = result.artifacts
    assert ref.size_bytes == len(artifact.content)
    assert ref.url == f"{record.links.self}/artifacts/schemathesis-stub.json"


async def test_run_times_out() -> None:
    record = make_record({"tool": "schemathesis"}, {"tool": "microcks"})

    await StubEngine(Settings(tool_delay_s=5)).run(record, timeout_s=0.05)

    assert record.state == RunState.TIMED_OUT
    assert record.error is not None and record.error.code == ErrorCode.TIMEOUT
    first, second = record.tools
    assert first.state == ToolState.ERROR
    assert first.error is not None and first.error.code == ErrorCode.TIMEOUT
    assert second.state == ToolState.SKIPPED


async def test_cancel_running_run() -> None:
    engine = StubEngine(Settings(tool_delay_s=5))
    record = make_record({"tool": "schemathesis"}, {"tool": "microcks"})
    engine.start(record)
    while record.tools[0].state != ToolState.RUNNING:
        await asyncio.sleep(0)

    engine.cancel(record)
    assert record.task is not None
    await asyncio.gather(record.task, return_exceptions=True)

    assert record.state == RunState.CANCELLED
    assert record.finished_at is not None
    assert [t.state for t in record.tools] == [ToolState.CANCELLED, ToolState.SKIPPED]


async def test_cancel_before_start() -> None:
    engine = StubEngine(Settings(tool_delay_s=5))
    record = make_record({"tool": "schemathesis"})
    engine.start(record)

    engine.cancel(record)
    assert record.task is not None
    await asyncio.gather(record.task, return_exceptions=True)

    assert record.state == RunState.CANCELLED
    assert [t.state for t in record.tools] == [ToolState.SKIPPED]
