"""SchemathesisRunner against the buggy petstore: a real `st run` subprocess."""

import json
from pathlib import Path

import pytest

from testing_tools.api.schemas import (
    ErrorCode,
    SchemathesisCheck,
    SchemathesisConfig,
    SchemathesisResult,
    Severity,
    ToolState,
)
from testing_tools.app.contract import LoadedContract
from testing_tools.app.tools.base import ToolError
from testing_tools.app.tools.schemathesis import SchemathesisRunner, _command
from tool_helpers import make_ctx

pytestmark = pytest.mark.anyio


def config(**fields: object) -> SchemathesisConfig:
    base = {"max_examples": 10, "seed": 1, "phases": ["examples", "coverage", "fuzzing"]}
    return SchemathesisConfig.model_validate({**base, **fields})


async def test_finds_the_planted_bugs(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, collected = make_ctx(tmp_path, contract, petstore_url, headers={"X-Api-Key": "secret"})

    result = await SchemathesisRunner().run(config(), ctx)

    assert isinstance(result, SchemathesisResult)
    assert result.state == ToolState.FAILED
    assert result.seed == 1
    assert result.tool_version
    summary = result.summary
    assert summary is not None
    assert (summary.operations_selected, summary.operations_tested) == (3, 3)
    assert summary.requests > 0 and summary.failures == len(result.findings)

    schema_bug = next(f for f in result.findings if f.check == "response_schema_conformance")
    assert schema_bug.severity == Severity.HIGH
    assert schema_bug.operation.operation_id == "getPet"
    assert '"1" is not of type "integer"' in schema_bug.message
    assert schema_bug.request is not None and schema_bug.request.url.endswith("/pets/1")
    assert schema_bug.request.headers["X-Api-Key"] == "***"
    assert schema_bug.response is not None and schema_bug.response.status_code == 200
    assert schema_bug.reproduce is not None and "secret" not in schema_bug.reproduce
    assert len({f.id for f in result.findings}) == len(result.findings)

    coverage = result.coverage
    assert coverage is not None
    assert coverage.operations_tested == 3
    get_pet = coverage.by_operation[2]
    assert 200 in get_pet.observed_status_codes and get_pet.requests > 0

    assert {"schemathesis-report.json", "schemathesis-events.ndjson", "schemathesis-junit.xml",
            "schemathesis.har", "schemathesis-output.log"} <= set(collected.artifacts)  # fmt: skip
    assert json.loads(collected.artifacts["schemathesis-report.json"][1])["seed"] == 1
    assert collected.progress[-1] == 0.95


async def test_filter_limits_operations(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)
    cfg = config(
        operations={"include": {"operation_ids": ["listPets"]}},
        checks=["not_a_server_error", "response_schema_conformance"],
    )

    result = await SchemathesisRunner().run(cfg, ctx)

    assert isinstance(result, SchemathesisResult)
    assert result.state == ToolState.PASSED
    assert result.summary is not None and result.summary.operations_selected == 1
    assert result.coverage is not None
    assert [c.tested for c in result.coverage.by_operation] == [True, False, False]


async def test_empty_selection_is_an_error(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)

    with pytest.raises(ToolError) as exc:
        await SchemathesisRunner().run(config(operations={"include": {"tags": ["nope"]}}), ctx)

    assert exc.value.code == ErrorCode.TOOL_FAILED


async def test_unreachable_target_still_reports(tmp_path: Path, contract: LoadedContract) -> None:
    ctx, collected = make_ctx(tmp_path, contract, "http://127.0.0.1:9", request_timeout_s=1)

    result = await SchemathesisRunner().run(config(phases=["examples"]), ctx)

    assert isinstance(result, SchemathesisResult)
    assert result.summary is not None and result.summary.errors > 0
    assert "schemathesis-output.log" in collected.artifacts


def test_command_maps_config_to_cli_flags(tmp_path: Path, contract: LoadedContract) -> None:
    ctx, _ = make_ctx(
        tmp_path, contract, "http://svc:8080/api/", headers={"A": "b"}, tls_verify=False
    )
    cfg = config(
        checks=[SchemathesisCheck.NOT_A_SERVER_ERROR, SchemathesisCheck.MAX_RESPONSE_TIME],
        exclude_checks=[SchemathesisCheck.IGNORED_AUTH],
        max_response_time_ms=250,
        max_failures=3,
        workers=2,
        mode="negative",
    )

    args = _command(cfg, ctx, tmp_path / "s.json", tmp_path / "r", seed=5)

    assert args[1:5] == ["-m", "schemathesis.cli", "run", str(tmp_path / "s.json")]
    assert "--url" in args and args[args.index("--url") + 1] == "http://svc:8080/api"
    assert args[args.index("--checks") + 1] == "not_a_server_error"
    assert args[args.index("--exclude-checks") + 1] == "ignored_auth"
    assert args[args.index("--max-response-time") + 1] == "0.25"
    assert args[args.index("--max-failures") + 1] == "3"
    assert args[args.index("--workers") + 1] == "2"
    assert args[args.index("--mode") + 1] == "negative"
    assert args[args.index("--seed") + 1] == "5"
    assert args[args.index("--tls-verify") + 1] == "false"
    assert args[args.index("--header") + 1] == "A: b"


def test_command_defaults_to_all_checks_without_time_limit(
    tmp_path: Path, contract: LoadedContract
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, "http://svc")

    args = _command(config(), ctx, tmp_path / "s.json", tmp_path / "r", seed=5)

    assert args[args.index("--checks") + 1] == "all"
    assert "--max-response-time" not in args
    assert "--tls-verify" not in args
