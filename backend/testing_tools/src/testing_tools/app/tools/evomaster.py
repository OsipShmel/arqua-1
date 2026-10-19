"""EvoMaster in black-box mode via its jar; results come from its WFC report (report.json).

WFC — Web Fuzzing Commons: a shared report format with numbered fault categories
(100 "HTTP Status 500", 200 "Schema Violation", 3xx security, ...).
"""

import asyncio
import hashlib
import io
import json
import random
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from testing_tools.api.schemas import (
    ErrorCode,
    EvoMasterConfig,
    EvoMasterResult,
    EvoMasterSummary,
    Finding,
    HttpMethod,
    Severity,
    ToolConfig,
    ToolName,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import ContractOperation, select_operations
from testing_tools.app.tools.base import ToolContext, ToolError
from testing_tools.app.tools.common import (
    Observations,
    OperationIndex,
    build_coverage,
    filtered_document,
)
from testing_tools.app.tools.process import run_process

MAX_HEADERS = 3  # EvoMaster takes --header0..--header2
OVERHEAD_S = 120  # minimization and output writing come on top of the search budget

# WFC fault categories: code -> (id, title)
CATEGORIES: dict[int, tuple[str, str]] = {
    100: ("HTTP_STATUS_500", "HTTP Status 500"),
    101: ("HTTP_STATUS_NO_NON_STANDARD_CODES", "Non-standard HTTP status code"),
    102: ("HTTP_STATUS_NO_201_IF_DELETE", "201 on DELETE"),
    103: ("HTTP_STATUS_NO_201_IF_GET", "201 on GET"),
    104: ("HTTP_STATUS_NO_201_IF_PATCH", "201 on PATCH"),
    105: ("HTTP_STATUS_NO_204_IF_CONTENT", "204 with a body"),
    106: ("HTTP_STATUS_NO_413_IF_NO_PAYLOAD", "413 without a payload"),
    107: ("HTTP_STATUS_NO_415_IF_NO_PAYLOAD", "415 without a payload"),
    108: ("HTTP_STATUS_NO_304_IF_NO_GET_OR_HEAD", "304 not on GET/HEAD"),
    109: ("HTTP_STATUS_NO_401_IF_NO_WWW_AUTHENTICATE", "401 without WWW-Authenticate"),
    110: ("HTTP_STATUS_NO_405_IF_NO_ALLOW", "405 without Allow"),
    111: ("HTTP_STATUS_NO_205_IF_CONTENT", "205 with a body"),
    112: ("HTTP_STATUS_NO_426_IF_NO_UPGRADE", "426 without Upgrade"),
    113: ("HTTP_NONWORKING_DELETE", "Resource still accessible after successful DELETE"),
    114: ("HTTP_SIDE_EFFECTS_FAILED_MODIFICATION", "Failed PUT/PATCH changed the resource"),
    115: ("HTTP_REPEATED_CREATE_PUT", "Repeated PUT creates with 201"),
    116: ("HTTP_MISLEADING_CREATE_PUT", "PUT 201 for an existing resource"),
    117: ("HTTP_PARTIAL_UPDATE_PUT", "PUT is not a full replacement"),
    118: ("HTTP_NON_IDEMPOTENT_PUT", "PUT is not idempotent"),
    119: ("HTTP_INVALID_MERGE_PATCH", "Invalid JSON Merge Patch"),
    120: ("HTTP_INVALID_LOCATION", "Invalid Location header"),
    121: ("HTTP_OTHER_STATUS_5XX", "HTTP 5xx other than 500 and 501"),
    122: ("HTTP_CREATED_RESOURCE_NOT_FOUND", "Resource not found right after creation"),
    200: ("SCHEMA_INVALID_RESPONSE", "Response does not match the schema"),
    201: ("SCHEMA_INVALID_ALLOW", "Invalid Allow header"),
    202: ("SCHEMA_STATUS_NO_401_IF_NO_AUTH", "401 although the API declares no auth"),
    203: ("SCHEMA_STATUS_NO_403_IF_NO_401", "403 without a declared 401"),
    204: ("SCHEMA_STATUS_HAS_406_IF_ACCEPT", "406 for an acceptable request"),
    205: ("SCHEMA_STATUS_NO_501_IF_IMPLEMENTED", "501 on a declared endpoint"),
    206: ("SCHEMA_VALIDATION_BYPASS", "Success response to invalid data"),
    207: ("SCHEMA_VALID_INPUT_REJECTED", "User error response to valid data"),
    300: ("SECURITY_SQL_INJECTION", "SQL injection"),
    301: ("SECURITY_XSS_INJECTION", "Cross-site scripting"),
    302: ("SECURITY_SSRF", "Server-side request forgery"),
    303: ("SECURITY_MASS_ASSIGNMENT", "Mass assignment"),
    304: ("SECURITY_EXISTENCE_LEAKAGE", "Leaks existence of a protected resource"),
    305: ("SECURITY_NOT_RECOGNIZED_AUTHENTICATED", "Valid credentials not recognized"),
    306: ("SECURITY_INCONSISTENT_WRITE_AUTHORIZATION", "Modifiable resource that should be protected"),
    307: ("SECURITY_IGNORE_ANONYMOUS", "Protected resource accessible anonymously"),
    308: ("SECURITY_ANONYMOUS_MODIFICATIONS", "Anonymous modifications"),
    309: ("SECURITY_LEAKED_STACK_TRACES", "Leaked stack trace"),
    310: ("SECURITY_HIDDEN_ACCESSIBLE_ENDPOINT", "Hidden accessible endpoint"),
    311: ("SECURITY_DECLARED_AUTH_NOT_ENFORCED", "Declared authentication is not enforced"),
    312: ("SECURITY_CALL_TIMEOUT", "Call timeout"),
}
CRITICAL = {100, 300, 301, 302, 307, 311}
HIGH = {113, 114, 121, 122, 200, 206, 303, 304, 305, 306, 309, 310}


def severity(code: int) -> Severity:
    if code in CRITICAL:
        return Severity.CRITICAL
    if code in HIGH:
        return Severity.HIGH
    if code in (308, 312) or 100 <= code < 300:
        return Severity.MEDIUM
    return Severity.LOW


class EvoMasterRunner:
    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        assert isinstance(config, EvoMasterConfig)
        jar = ctx.settings.evomaster_jar
        if not jar or not Path(jar).is_file():
            raise ToolError(ErrorCode.TOOL_UNAVAILABLE, "TT_EVOMASTER_JAR is not set or missing")
        if len(ctx.target.headers) > MAX_HEADERS:
            raise ToolError(
                ErrorCode.TOOL_FAILED,
                f"EvoMaster takes at most {MAX_HEADERS} target headers, "
                f"got {len(ctx.target.headers)}",
            )
        selected = select_operations(ctx.contract.operations, config.operations)
        if not selected:
            raise ToolError(ErrorCode.TOOL_FAILED, "no contract operations match the filter")

        schema = ctx.work_dir / "openapi.json"
        schema.write_text(json.dumps(evomaster_document(ctx, selected)))
        out = ctx.work_dir / "out"
        seed = config.seed if config.seed is not None else random.randrange(2**31)

        ticker = asyncio.create_task(_tick(ctx, config.max_time_s))
        try:
            proc = await run_process(
                _command(config, ctx, Path(jar), schema, out, seed),
                cwd=ctx.work_dir,
                timeout_s=config.max_time_s * 2 + OVERHEAD_S,
            )
        finally:
            ticker.cancel()
        ctx.set_progress(0.95)
        ctx.add_artifact("evomaster-output.log", "text/plain", proc.output)
        report_path = out / "report.json"
        if report_path.exists():
            ctx.add_artifact("evomaster-report.json", "application/json", report_path.read_bytes())
        tests_zip = _zip_tests(out)
        if tests_zip:
            ctx.add_artifact("evomaster-tests.zip", "application/zip", tests_zip)

        if proc.timed_out:
            raise ToolError(ErrorCode.TIMEOUT, "EvoMaster did not finish in time",
                            {"output_tail": proc.tail()})  # fmt: skip
        if proc.returncode != 0 or not report_path.exists():
            raise ToolError(
                ErrorCode.TOOL_FAILED,
                f"EvoMaster exited with code {proc.returncode}",
                {"output_tail": proc.tail()},
            )
        report: dict[str, Any] = json.loads(report_path.read_text())
        return _result(ctx, report, seed, len(selected))


def evomaster_document(ctx: ToolContext, selected: list[ContractOperation]) -> dict[str, Any]:
    """The contract as EvoMaster wants it.

    - only the selected operations;
    - path-level `parameters` copied into every operation (EvoMaster ignores them);
    - `servers` pointing at the target, so its base path (e.g. /api) is kept.
    """
    doc = filtered_document(ctx.contract, selected)
    for item in (doc.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        shared = item.pop("parameters", None)
        if not isinstance(shared, list):
            continue
        for method in HttpMethod:
            op = item.get(method.value.lower())
            if not isinstance(op, dict):
                continue
            own = op.get("parameters") or []
            names = {(p.get("in"), p.get("name")) for p in own if isinstance(p, dict)}
            inherited = [
                p for p in shared if not isinstance(p, dict) or (p.get("in"), p.get("name"))
                not in names
            ]  # fmt: skip
            op["parameters"] = own + inherited
    doc["servers"] = [{"url": ctx.base_url}]
    return doc


def _command(
    config: EvoMasterConfig, ctx: ToolContext, jar: Path, schema: Path, out: Path, seed: int
) -> list[str]:
    url = urlsplit(ctx.base_url)
    args = [
        ctx.settings.java_bin, "-jar", str(jar),
        "--blackBox", "true",
        "--schema", str(schema),
        "--base", f"{url.scheme}://{url.netloc}",
        "--maxTime", f"{config.max_time_s}s",
        "--seed", str(seed),
        "--outputFolder", str(out),
        "--outputFormat", "PYTHON_UNITTEST",
        "--schemaOracles", str(config.schema_oracles).lower(),
        "--writeWFCReport", "true",
        "--writeWFCReportExcludeWebApp", "true",
        "--showProgress", "false",
    ]  # fmt: skip
    if config.disabled_oracle_codes:
        args += ["--disabledOracleCodes", ",".join(map(str, config.disabled_oracle_codes))]
    if config.rate_per_minute is not None:
        args += ["--ratePerMinute", str(config.rate_per_minute)]
    for i, (name, value) in enumerate(ctx.target.headers.items()):
        args += [f"--header{i}", f"{name}:{value}"]
    return args


async def _tick(ctx: ToolContext, budget_s: int) -> None:
    started = time.monotonic()
    while True:
        await asyncio.sleep(1)
        ctx.set_progress(min(0.9, 0.9 * (time.monotonic() - started) / budget_s))


def _zip_tests(out: Path) -> bytes | None:
    files = sorted(out.glob("*.py")) if out.is_dir() else []
    if not files:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
    return buffer.getvalue()


def _result(
    ctx: ToolContext, report: dict[str, Any], seed: int, selected_count: int
) -> EvoMasterResult:
    index = OperationIndex(ctx.contract.operations)
    rest = (report.get("problemDetails") or {}).get("rest") or {}

    observed = Observations()
    for item in rest.get("coveredHttpStatus") or []:
        op = index.by_label(_label(item.get("endpointId", "")))
        if op is None:
            continue  # e.g. OPTIONS probes on a documented path
        for status in item.get("httpStatus") or [None]:
            observed.add(op, status)

    faults = (report.get("faults") or {}).get("foundFaults") or []
    findings: dict[str, Finding] = {}
    by_category: Counter[str] = Counter()
    for fault in faults:
        op = index.by_label(_label(fault.get("operationId", "")))
        for category in fault.get("faultCategories") or []:
            code = int(category.get("code", 0))
            category_id, title = CATEGORIES.get(code, (f"WFC_{code}", f"WFC fault {code}"))
            by_category[category_id] += 1
            if op is None:
                continue
            context = category.get("context") or title
            key = "\x00".join((str(code), op.ref.method.value, op.ref.path, context))
            findings.setdefault(
                key,
                Finding(
                    id="em-" + hashlib.sha1(key.encode()).hexdigest()[:10],
                    tool=ToolName.EVOMASTER,
                    check=category_id.lower(),
                    severity=severity(code),
                    operation=op.ref,
                    title=title,
                    message=f"{context} (EvoMaster test {fault.get('testCaseId', '?')})",
                ),
            )

    unique = list(findings.values())
    tests = [str(t.get("id")) for t in report.get("testCases") or [] if t.get("id")]
    return EvoMasterResult(
        state=ToolState.FAILED if unique else ToolState.PASSED,
        tool_version=report.get("toolVersion"),
        seed=seed,
        summary=EvoMasterSummary(
            operations_selected=selected_count,
            tests_generated=int(report.get("totalTests") or len(tests)),
            http_calls_evaluated=int(rest.get("evaluatedHttpCalls") or 0),
            faults=int((report.get("faults") or {}).get("totalNumber") or len(faults)),
            faults_by_category=dict(by_category),
        ),
        coverage=build_coverage(ctx.contract.operations, observed),
        findings=unique,
        generated_tests=tests,
    )


def _label(endpoint_id: str) -> str:
    """WFC endpoint ids look like "GET:/pets/{petId}"; ours like "GET /pets/{petId}"."""
    method, _, path = endpoint_id.partition(":")
    return f"{method} {path}"

