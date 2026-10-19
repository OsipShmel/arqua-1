"""EvoMasterRunner: report parsing on a real WFC report, the command line, and a live run
against the buggy petstore when an evomaster.jar is available (EVOMASTER_JAR)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from testing_tools.api.schemas import (
    ErrorCode,
    EvoMasterConfig,
    EvoMasterResult,
    Severity,
    ToolState,
)
from petstore import PETSTORE_JSON
from testing_tools.app.contract import LoadedContract, parse_contract
from testing_tools.app.settings import Settings
from testing_tools.app.tools.base import ToolError
from testing_tools.app.tools.evomaster import (
    EvoMasterRunner,
    _command,
    _result,
    evomaster_document,
    severity,
)
from tool_helpers import make_ctx

pytestmark = pytest.mark.anyio

REPORT = Path(__file__).parent / "data" / "evomaster_report.json"
JAR = os.environ.get("EVOMASTER_JAR", "")
live = pytest.mark.skipif(
    not (JAR and Path(JAR).is_file() and shutil.which("java")),
    reason="set EVOMASTER_JAR to an evomaster.jar (and have java) to run EvoMaster live",
)


def test_result_from_real_report(tmp_path: Path, contract: LoadedContract) -> None:
    ctx, _ = make_ctx(tmp_path, contract, "http://127.0.0.1:18080")

    result = _result(ctx, json.loads(REPORT.read_text()), seed=1, selected_count=3)

    assert result.state == ToolState.FAILED
    assert result.tool_version == "6.2.0"
    assert result.summary is not None
    assert result.summary.tests_generated == 12
    assert result.summary.faults_by_category == {
        "SCHEMA_INVALID_RESPONSE": 2,
        "HTTP_STATUS_500": 1,
    }
    by_check = {(f.check, f.operation.operation_id): f for f in result.findings}
    server_error = by_check[("http_status_500", "createPet")]
    assert server_error.severity == Severity.CRITICAL
    assert "EvoMaster_faults_Test.py#" in server_error.message
    schema = by_check[("schema_invalid_response", "getPet")]
    assert "schema.type" in schema.message  # the planted `id` as string
    assert len(result.findings) == 3
    assert result.generated_tests and result.generated_tests[0].startswith("EvoMaster_")

    coverage = result.coverage
    assert coverage is not None
    assert coverage.operations_tested == 3
    # OPTIONS probes on documented paths are not counted as covering an operation
    assert [c.observed_status_codes for c in coverage.by_operation] == [
        [200],
        [201, 500],  # the planted crash on an empty name
        [200, 404],
    ]


def test_document_inlines_path_parameters_and_sets_server(tmp_path: Path) -> None:
    contract = parse_contract(PETSTORE_JSON)  # /pets/{petId} declares petId at path level
    ctx, _ = make_ctx(tmp_path, contract, "http://svc:8080/api")

    doc = evomaster_document(ctx, list(contract.operations[:3]))

    item = doc["paths"]["/pets/{petId}"]
    assert "parameters" not in item
    assert [p["name"] for p in item["get"]["parameters"]] == ["petId"]
    assert "delete" not in item  # filtered out
    assert doc["servers"] == [{"url": "http://svc:8080/api"}]
    assert "parameters" in contract.document["paths"]["/pets/{petId}"]  # original untouched


def test_command(tmp_path: Path, contract: LoadedContract) -> None:
    settings = Settings(tool_mode="real", java_bin="/opt/java")
    ctx, _ = make_ctx(
        tmp_path, contract, "http://svc:8080/api/", settings, headers={"X-Key": "k"}
    )
    cfg = EvoMasterConfig(
        max_time_s=45, disabled_oracle_codes=[308, 312], rate_per_minute=600, schema_oracles=False
    )

    args = _command(cfg, ctx, Path("/em.jar"), tmp_path / "s.json", tmp_path / "out", seed=9)

    def flag(name: str) -> str:
        return args[args.index(name) + 1]

    assert args[:3] == ["/opt/java", "-jar", "/em.jar"]
    assert flag("--base") == "http://svc:8080"  # the path comes from `servers`
    assert flag("--maxTime") == "45s"
    assert flag("--seed") == "9"
    assert flag("--outputFormat") == "PYTHON_UNITTEST"
    assert flag("--schemaOracles") == "false"
    assert flag("--disabledOracleCodes") == "308,312"
    assert flag("--ratePerMinute") == "600"
    assert flag("--header0") == "X-Key:k"


def test_severity() -> None:
    assert severity(100) == Severity.CRITICAL
    assert severity(200) == Severity.HIGH
    assert severity(110) == Severity.MEDIUM
    assert severity(308) == Severity.MEDIUM
    assert severity(999) == Severity.LOW


async def test_missing_jar_is_unavailable(tmp_path: Path, contract: LoadedContract) -> None:
    settings = Settings(tool_mode="real", evomaster_jar=str(tmp_path / "nope.jar"))
    ctx, _ = make_ctx(tmp_path, contract, "http://127.0.0.1:9", settings)

    with pytest.raises(ToolError) as exc:
        await EvoMasterRunner().run(EvoMasterConfig(), ctx)

    assert exc.value.code == ErrorCode.TOOL_UNAVAILABLE
    assert not settings.tool_available(EvoMasterConfig().tool)


async def test_too_many_headers(tmp_path: Path, contract: LoadedContract) -> None:
    jar = tmp_path / "em.jar"
    jar.write_bytes(b"")
    settings = Settings(tool_mode="real", evomaster_jar=str(jar))
    headers = {f"H{i}": "v" for i in range(4)}
    ctx, _ = make_ctx(tmp_path, contract, "http://127.0.0.1:9", settings, headers=headers)

    with pytest.raises(ToolError) as exc:
        await EvoMasterRunner().run(EvoMasterConfig(), ctx)

    assert exc.value.code == ErrorCode.TOOL_FAILED


async def test_crash_reports_output(tmp_path: Path, contract: LoadedContract) -> None:
    jar = tmp_path / "em.jar"
    jar.write_bytes(b"not a jar")
    java = shutil.which("false") or "false"  # a "java" that exits 1 at once
    settings = Settings(tool_mode="real", evomaster_jar=str(jar), java_bin=java)
    ctx, collected = make_ctx(tmp_path, contract, "http://127.0.0.1:9", settings)

    with pytest.raises(ToolError) as exc:
        await EvoMasterRunner().run(EvoMasterConfig(max_time_s=10), ctx)

    assert exc.value.code == ErrorCode.TOOL_FAILED
    assert "evomaster-output.log" in collected.artifacts


@live
async def test_live_run(tmp_path: Path, contract: LoadedContract, petstore_url: str) -> None:
    settings = Settings(tool_mode="real", evomaster_jar=JAR)
    ctx, collected = make_ctx(tmp_path, contract, petstore_url, settings)

    result = await EvoMasterRunner().run(EvoMasterConfig(max_time_s=10, seed=1), ctx)

    assert isinstance(result, EvoMasterResult)
    assert result.state == ToolState.FAILED
    checks = {(f.check, f.operation.operation_id) for f in result.findings}
    assert ("schema_invalid_response", "getPet") in checks  # `id` comes back as a string
    assert {"evomaster-report.json", "evomaster-tests.zip"} <= set(collected.artifacts)
