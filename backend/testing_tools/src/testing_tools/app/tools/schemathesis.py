"""Schemathesis via its CLI (`st run`) in a subprocess; results come from its JSON/NDJSON reports."""

import base64
import hashlib
import json
import random
import sys
from collections import Counter
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from testing_tools.api.schemas import (
    ErrorCode,
    Finding,
    SchemathesisCheck,
    SchemathesisConfig,
    SchemathesisResult,
    SchemathesisSummary,
    Severity,
    ToolConfig,
    ToolName,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import ContractOperation, select_operations
from testing_tools.app.tools.base import ToolContext, ToolError
from testing_tools.app.tools.common import (
    Masker,
    Observations,
    OperationIndex,
    build_coverage,
    curl,
    filtered_document,
    request_snapshot,
    response_snapshot,
)
from testing_tools.app.tools.process import run_process

# exit codes of `st run`: 0 - no failures, 1 - failures found; anything else is a crash
OK_EXIT_CODES = {0, 1}
REPORTS = {
    "json": ("schemathesis-report.json", "application/json"),
    "ndjson": ("schemathesis-events.ndjson", "application/x-ndjson"),
    "junit": ("schemathesis-junit.xml", "application/xml"),
    "har": ("schemathesis.har", "application/json"),
}
# max_response_time is a CLI option in Schemathesis, not a check name
OPTION_CHECKS = {SchemathesisCheck.MAX_RESPONSE_TIME}

SEVERITY: dict[str, Severity] = {
    "not_a_server_error": Severity.CRITICAL,
    "ignored_auth": Severity.CRITICAL,
    "response_schema_conformance": Severity.HIGH,
    "status_code_conformance": Severity.HIGH,
    "use_after_free": Severity.HIGH,
    "ensure_resource_availability": Severity.HIGH,
    "negative_data_rejection": Severity.MEDIUM,
    "positive_data_acceptance": Severity.MEDIUM,
    "missing_required_header": Severity.MEDIUM,
    "unsupported_method": Severity.MEDIUM,
    "content_type_conformance": Severity.MEDIUM,
    "response_headers_conformance": Severity.LOW,
    "max_response_time": Severity.LOW,
}


def schemathesis_version() -> str | None:
    try:
        return version("schemathesis")
    except PackageNotFoundError:
        return None


class SchemathesisRunner:
    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        assert isinstance(config, SchemathesisConfig)
        selected = select_operations(ctx.contract.operations, config.operations)
        if not selected:
            raise ToolError(ErrorCode.TOOL_FAILED, "no contract operations match the filter")

        schema_path = ctx.work_dir / "openapi.json"
        schema_path.write_text(json.dumps(filtered_document(ctx.contract, selected)))
        reports = ctx.work_dir / "reports"
        seed = config.seed if config.seed is not None else random.randrange(2**31)

        ctx.set_progress(0.05)
        proc = await run_process(
            _command(config, ctx, schema_path, reports, seed),
            cwd=ctx.work_dir,
            timeout_s=None,  # bounded by the run timeout
        )
        ctx.set_progress(0.95)
        ctx.add_artifact("schemathesis-output.log", "text/plain", proc.output)
        for fmt, (name, media_type) in REPORTS.items():
            path = reports / name
            if path.exists():
                ctx.add_artifact(name, media_type, path.read_bytes())

        report_path = reports / REPORTS["json"][0]
        if proc.returncode not in OK_EXIT_CODES or not report_path.exists():
            raise ToolError(
                ErrorCode.TOOL_FAILED,
                f"schemathesis exited with code {proc.returncode}",
                {"output_tail": proc.tail()},
            )
        report: dict[str, Any] = json.loads(report_path.read_text())
        events = reports / REPORTS["ndjson"][0]
        return _result(ctx, report, events, len(selected))


def _command(
    config: SchemathesisConfig, ctx: ToolContext, schema: Path, reports: Path, seed: int
) -> list[str]:
    args = [
        sys.executable, "-m", "schemathesis.cli", "run", str(schema),
        "--url", ctx.base_url,
        "--phases", ",".join(p.value for p in config.phases),
        "--mode", config.mode.value,
        "--max-examples", str(config.max_examples),
        "--seed", str(seed),
        "--workers", str(config.workers),
        "--request-timeout", str(ctx.target.request_timeout_s),
        "--generation-database", "none",
        "--no-color",
        "--report", ",".join(REPORTS),
        "--report-dir", str(reports),
    ]  # fmt: skip
    for fmt, (name, _) in REPORTS.items():
        args += [f"--report-{fmt}-path", str(reports / name)]

    checks = [c for c in config.checks or [] if c not in OPTION_CHECKS]
    if config.checks is None:
        args += ["--checks", "all"]
    elif checks:
        args += ["--checks", ",".join(c.value for c in checks)]
    excluded = [c for c in config.exclude_checks if c not in OPTION_CHECKS]
    if excluded:
        args += ["--exclude-checks", ",".join(c.value for c in excluded)]

    wants_time_check = (
        config.checks is None or SchemathesisCheck.MAX_RESPONSE_TIME in config.checks
    ) and SchemathesisCheck.MAX_RESPONSE_TIME not in config.exclude_checks
    if config.max_response_time_ms is not None and wants_time_check:
        args += ["--max-response-time", str(config.max_response_time_ms / 1000)]
    if config.max_failures is not None:
        args += ["--max-failures", str(config.max_failures)]
    if not ctx.target.tls_verify:
        args += ["--tls-verify", "false"]
    for name, value in ctx.target.headers.items():
        args += ["--header", f"{name}: {value}"]
    return args


def _result(
    ctx: ToolContext, report: dict[str, Any], events_path: Path, selected_count: int
) -> SchemathesisResult:
    index = OperationIndex(ctx.contract.operations)
    masker = Masker(ctx.target.headers)
    observed = Observations()
    findings: dict[str, Finding] = {}
    requests = 0

    for scenario in _scenarios(events_path):
        recorder = scenario.get("recorder") or {}
        op = index.by_label(recorder.get("label", ""))
        interactions: dict[str, Any] = recorder.get("interactions") or {}
        for interaction in interactions.values():
            requests += 1
            request = interaction.get("request") or {}
            response = interaction.get("response")
            # requests with a foreign method (unsupported_method probes) don't cover the op
            if op is not None and request.get("method", "").upper() == op.ref.method.value:
                observed.add(op, response.get("status_code") if response else None)
        if op is None:
            continue
        for case_id, checks in (recorder.get("checks") or {}).items():
            for check in checks:
                if check.get("status") != "failure":
                    continue
                finding = _finding(check, op, interactions.get(case_id), masker)
                findings.setdefault(finding.id, finding)

    operations = report.get("operations") or {}
    unique = list(findings.values())
    return SchemathesisResult(
        state=ToolState.FAILED if unique else ToolState.PASSED,
        tool_version=report.get("schemathesis_version") or schemathesis_version(),
        seed=report.get("seed"),
        summary=SchemathesisSummary(
            operations_selected=operations.get("selected", selected_count),
            operations_tested=operations.get("tested", 0),
            test_cases=(report.get("test_cases") or {}).get("generated", 0),
            requests=requests,
            failures=len(unique),
            failures_by_check=dict(Counter(f.check for f in unique)),
            errors=len(report.get("errors") or []),
        ),
        coverage=build_coverage(ctx.contract.operations, observed),
        findings=unique,
    )


def _scenarios(events_path: Path) -> Iterator[dict[str, Any]]:
    if not events_path.exists():
        return
    with events_path.open() as lines:
        for line in lines:
            if '"ScenarioFinished"' not in line:
                continue
            event = json.loads(line).get("ScenarioFinished")
            if isinstance(event, dict):
                yield event


def _finding(
    check: dict[str, Any], op: ContractOperation, interaction: dict[str, Any] | None, masker: Masker
) -> Finding:
    failure = (check.get("failure_info") or {}).get("failure") or {}
    name: str = check.get("name", "unknown")
    title: str = failure.get("title") or name
    message: str = failure.get("message") or title
    key = "\x00".join((name, op.ref.method.value, op.ref.path, title, message))

    request = response = None
    if interaction:
        req = interaction.get("request") or {}
        request = request_snapshot(
            req.get("method", ""),
            req.get("uri", ""),
            _flat_headers(req.get("headers")),
            _decode(req.get("body")),
            masker,
        )
        resp = interaction.get("response")
        if resp:
            elapsed = resp.get("elapsed")
            response = response_snapshot(
                resp.get("status_code", 0),
                _flat_headers(resp.get("headers")),
                _decode(resp.get("content")),
                elapsed * 1000 if isinstance(elapsed, int | float) else None,
            )
    return Finding(
        id="st-" + hashlib.sha1(key.encode()).hexdigest()[:10],
        tool=ToolName.SCHEMATHESIS,
        check=name,
        severity=SEVERITY.get(name) or _severity(failure.get("severity")),
        operation=op.ref,
        title=title,
        message=message,
        request=request,
        response=response,
        reproduce=curl(request) if request else None,
    )


def _severity(value: Any) -> Severity:
    try:
        return Severity(str(value).lower())
    except ValueError:
        return Severity.MEDIUM


def _flat_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        str(k): ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        for k, v in headers.items()
    }


def _decode(body: Any) -> bytes | str | None:
    if isinstance(body, dict) and "$base64" in body:
        return base64.b64decode(body["$base64"])
    if isinstance(body, str):
        return body
    return None
