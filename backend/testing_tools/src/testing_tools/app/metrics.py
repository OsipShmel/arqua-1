"""One flat summary of the ТЗ metrics across all tools of a run."""

from collections import Counter
from collections.abc import Sequence

from testing_tools.api.schemas import (
    EvoMasterResult,
    FaultMetrics,
    MicrocksResult,
    MutationResult,
    RunMetrics,
    SchemathesisResult,
    ToolResult,
)
from testing_tools.app.contract import LoadedContract
from testing_tools.app.tools.common import Observations, OperationIndex, build_coverage, label

BlackBoxResult = SchemathesisResult | MicrocksResult | EvoMasterResult
BLACK_BOX = (SchemathesisResult, MicrocksResult, EvoMasterResult)


def run_metrics(contract: LoadedContract, results: Sequence[ToolResult]) -> RunMetrics:
    black_box = [r for r in results if isinstance(r, BLACK_BOX)]
    mutation = next((r for r in results if isinstance(r, MutationResult)), None)
    coverage = _union_coverage(contract, black_box)
    suite = mutation.coverage if mutation else None
    return RunMetrics(
        contract_coverage=coverage[0] if coverage else None,
        status_code_coverage=coverage[1] if coverage else None,
        faults=_faults(black_box),
        tests_contract_coverage=round(suite.operations_ratio, 4) if suite else None,
        tests_status_code_coverage=round(suite.status_codes_ratio, 4) if suite else None,
        test_suite_size=mutation.baseline.tests_total if mutation and mutation.baseline else None,
        runnability=mutation.baseline.runnability if mutation and mutation.baseline else None,
        mutation_score=(
            mutation.summary.mutation_score if mutation and mutation.summary else None
        ),
        duration_s={r.tool: r.duration_s for r in results if r.duration_s is not None},
    )


def _union_coverage(contract: LoadedContract, results: Sequence[BlackBoxResult]) -> tuple[float, float] | None:
    """An operation / status code counts as covered if any tool covered it."""
    reports = [r.coverage for r in results if r.coverage is not None]
    if not reports:
        return None
    index = OperationIndex(contract.operations)
    union = Observations()
    for report in reports:
        for item in report.by_operation:
            op = index.by_label(label(item.operation))
            if op is None or not item.tested:
                continue
            union.add(op, None)
            for status in item.observed_status_codes:
                union.add(op, status)
    merged = build_coverage(contract.operations, union)
    return round(merged.operations_ratio, 4), round(merged.status_codes_ratio, 4)


def _faults(results: Sequence[BlackBoxResult]) -> FaultMetrics | None:
    if not results:
        return None
    findings = [f for r in results for f in r.findings]
    return FaultMetrics(
        findings_total=len(findings),
        by_severity=dict(Counter(f.severity for f in findings)),
        by_tool=dict(Counter(f.tool for f in findings)),
        operations_with_findings=len({label(f.operation) for f in findings}),
    )
