"""Plausible tool results derived from the contract; no tool is actually run."""

import random
from collections.abc import Sequence
from datetime import datetime
from pathlib import PurePosixPath

from testing_tools.api.schemas import (
    BaselineResult,
    ContractCoverage,
    InlineTestSuite,
    MicrocksConfig,
    MicrocksOperationResult,
    MicrocksResult,
    MicrocksStepResult,
    MicrocksSummary,
    MutationConfig,
    MutationResult,
    MutationSummary,
    OperationCoverage,
    SchemathesisConfig,
    SchemathesisResult,
    SchemathesisSummary,
    TestCaseResult,
    TestOutcome,
    ToolConfig,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import ContractOperation, LoadedContract, select_operations


def build_result(
    config: ToolConfig,
    contract: LoadedContract,
    version: str,
    started_at: datetime,
    finished_at: datetime,
) -> ToolResult:
    common = {
        "tool_version": version,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": (finished_at - started_at).total_seconds(),
    }
    match config:
        case SchemathesisConfig():
            return _schemathesis(config, contract).model_copy(update=common)
        case MicrocksConfig():
            return _microcks(config, contract).model_copy(update=common)
        case MutationConfig():
            return _mutation(config).model_copy(update=common)


def _schemathesis(config: SchemathesisConfig, contract: LoadedContract) -> SchemathesisResult:
    selected = select_operations(contract.operations, config.operations)
    cases = config.max_examples * len(selected)
    return SchemathesisResult(
        state=ToolState.PASSED,
        seed=config.seed if config.seed is not None else random.randrange(2**31),
        summary=SchemathesisSummary(
            operations_selected=len(selected),
            operations_tested=len(selected),
            test_cases=cases,
            requests=cases,
            failures=0,
            failures_by_check={},
            errors=0,
        ),
        coverage=_coverage(contract.operations, selected, config.max_examples),
    )


def _microcks(config: MicrocksConfig, contract: LoadedContract) -> MicrocksResult:
    wanted = set(config.filtered_operations) if config.filtered_operations is not None else None
    tested = [op for op in contract.operations if wanted is None or _microcks_name(op) in wanted]
    name = config.service_name or contract.info.title
    version = config.service_version or contract.info.version
    return MicrocksResult(
        state=ToolState.PASSED,
        service_ref=f"{name}:{version}",
        summary=MicrocksSummary(
            operations_total=len(tested),
            operations_passed=len(tested),
            operations_failed=0,
            steps_total=len(tested),
            steps_passed=len(tested),
        ),
        operations=[
            MicrocksOperationResult(
                operation=op.ref,
                microcks_operation=_microcks_name(op),
                success=True,
                steps=[MicrocksStepResult(request_name="stub-example", success=True)],
            )
            for op in tested
        ],
        coverage=_coverage(contract.operations, tested, requests_per_operation=1),
    )


def _mutation(config: MutationConfig) -> MutationResult:
    files = config.tests.files if isinstance(config.tests, InlineTestSuite) else {}
    tests = [
        TestCaseResult(node_id=f"{path}::test_stub", outcome=TestOutcome.PASSED, duration_s=0.0)
        for path in files
        if PurePosixPath(path).name.startswith("test_") and path.endswith(".py")
    ]
    runnability = 1.0 if tests else 0.0
    return MutationResult(
        # survived is always 0 here, so only runnability decides
        state=ToolState.PASSED if runnability == 1.0 else ToolState.FAILED,
        seed=config.seed,
        baseline=BaselineResult(
            tests_total=len(tests),
            passed=len(tests),
            failed=0,
            errors=0,
            skipped=0,
            runnability=runnability,
            duration_s=0.0,
            tests=tests,
        ),
        summary=MutationSummary(
            mutants_total=0,
            killed=0,
            survived=0,
            timeout=0,
            no_coverage=0,
            errors=0,
            mutation_score=0.0,
            by_operator={operator: 0.0 for operator in config.operators},
        ),
    )


def _coverage(
    operations: Sequence[ContractOperation],
    tested: Sequence[ContractOperation],
    requests_per_operation: int,
) -> ContractCoverage:
    by_operation = [
        OperationCoverage(
            operation=op.ref,
            tested=op in tested,
            documented_status_codes=list(op.status_codes),
            observed_status_codes=_observed(op) if op in tested else [],
            requests=requests_per_operation if op in tested else 0,
        )
        for op in operations
    ]
    documented = sum(len(op.status_codes) for op in operations)
    observed = sum(len(c.observed_status_codes) for c in by_operation)
    return ContractCoverage(
        operations_total=len(operations),
        operations_tested=len(tested),
        operations_ratio=len(tested) / len(operations) if operations else 0.0,
        status_codes_documented=documented,
        status_codes_observed=observed,
        status_codes_ratio=observed / documented if documented else 0.0,
        undocumented_status_codes_observed=0,
        by_operation=by_operation,
    )


def _observed(op: ContractOperation) -> list[int]:
    """One code per explicit or range key ("4XX" -> 400); `default` stays uncovered."""
    codes: list[int] = []
    for key in op.status_codes:
        if key.isdigit():
            codes.append(int(key))
        elif len(key) == 3 and key[0].isdigit() and key[1:].upper() == "XX":
            codes.append(int(key[0]) * 100)
    return codes


def _microcks_name(op: ContractOperation) -> str:
    return f"{op.ref.method.value} {op.ref.path}"
