"""Mutation testing of the agent's pytest suite through a response-mutating proxy.

1. The suite is fetched (inline files or git) and given a Python with pytest + httpx.
2. A proxy in front of the target is started; tests get its URL via `base_url_env`.
3. Baseline: the suite runs with no mutation -> runnability, and which operations and
   status codes the tests actually reach.
4. Every mutant is switched on in the proxy in turn and the baseline-passing tests are
   re-run. A test that passed on baseline and fails now kills the mutant.
"""

import random

from testing_tools.api.schemas import (
    BaselineResult,
    ErrorCode,
    Mutant,
    MutantState,
    MutationConfig,
    MutationOperator,
    MutationResult,
    MutationSummary,
    TestOutcome,
    ToolConfig,
    ToolResult,
    ToolState,
)
from testing_tools.app.contract import select_operations
from testing_tools.app.settings import VERSION
from testing_tools.app.tools.base import ToolContext, ToolError
from testing_tools.app.tools.common import Observations, OperationIndex, documented_key, label
from testing_tools.app.tools.mutation.mutants import MutantSpec, generate, sample
from testing_tools.app.tools.mutation.proxy import MutatingProxy, serve
from testing_tools.app.tools.mutation.suite import Suite, SuiteRun

LOG_TAIL = 4000
KILLING_OUTCOMES = {TestOutcome.FAILED, TestOutcome.ERROR}


class MutationRunner:
    async def run(self, config: ToolConfig, ctx: ToolContext) -> ToolResult:
        assert isinstance(config, MutationConfig)
        seed = config.seed if config.seed is not None else random.randrange(2**31)
        suite = await Suite.prepare(config, ctx.work_dir, ctx.settings.uv_bin)
        ctx.set_progress(0.05)

        selected = select_operations(ctx.contract.operations, config.operations)
        specs = sample(generate(ctx.contract.document, selected, config.operators),
                       config.max_mutants, seed)  # fmt: skip
        proxy = MutatingProxy(ctx.target, OperationIndex(ctx.contract.operations))
        log: list[str] = []

        async with serve(proxy) as url:
            junit = ctx.work_dir / "baseline-junit.xml"
            proxy.reset(None)
            baseline_run = await suite.run(config, url, config.per_mutant_timeout_s, junit=junit)
            reached = proxy.observed
            _check_baseline(baseline_run, config)
            log.append(f"==== baseline ====\n{baseline_run.process.output.decode(errors='replace')}")
            if junit.exists():
                ctx.add_artifact("mutation-baseline-junit.xml", "application/xml", junit.read_bytes())
            baseline = _baseline(baseline_run)
            ctx.set_progress(0.1)

            passed = [t.node_id for t in baseline_run.tests if t.outcome == TestOutcome.PASSED]
            failing = [t.node_id for t in baseline_run.tests if t.outcome != TestOutcome.PASSED]
            mutants: list[Mutant] = []
            for i, spec in enumerate(specs, start=1):
                if not passed or not _reached(spec, reached):
                    mutants.append(_mutant(spec, MutantState.NO_COVERAGE))
                else:
                    proxy.reset(spec)
                    run = await suite.run(
                        config, url, config.per_mutant_timeout_s, deselect=failing
                    )
                    mutants.append(_classify(spec, run, passed, proxy.applied))
                    log.append(_log_entry(mutants[-1], run))
                ctx.set_progress(0.1 + 0.85 * i / len(specs))

        ctx.add_artifact("mutation-pytest.log", "text/plain", "\n".join(log).encode())
        summary = _summary(mutants, config.operators)
        healthy = baseline.runnability == 1.0 and summary.survived == 0
        return MutationResult(
            state=ToolState.PASSED if healthy else ToolState.FAILED,
            tool_version=VERSION,
            seed=seed,
            baseline=baseline,
            summary=summary,
            mutants=mutants,
        )


def _check_baseline(run: SuiteRun, config: MutationConfig) -> None:
    if run.process.timed_out:
        raise ToolError(
            ErrorCode.TIMEOUT,
            f"baseline run exceeded per_mutant_timeout_s={config.per_mutant_timeout_s}",
            {"output_tail": run.process.tail()},
        )
    if run.broken:
        raise ToolError(
            ErrorCode.TEST_SUITE_INVALID,
            f"pytest could not run the suite (exit status {run.exitstatus})",
            {"output_tail": run.process.tail()},
        )


def _reached(spec: MutantSpec, observed: Observations) -> bool:
    """Did any baseline request get the response this mutant breaks?"""
    statuses = observed.statuses.get(label(spec.operation), set())
    return any(documented_key(spec.operation, s) == spec.status_key for s in statuses)


def _baseline(run: SuiteRun) -> BaselineResult:
    counts = {outcome: 0 for outcome in TestOutcome}
    for test in run.tests:
        counts[test.outcome] += 1
    errors = counts[TestOutcome.ERROR] + len(run.collection_errors)
    total = len(run.tests) + len(run.collection_errors)
    return BaselineResult(
        tests_total=total,
        passed=counts[TestOutcome.PASSED],
        failed=counts[TestOutcome.FAILED],
        errors=errors,
        skipped=counts[TestOutcome.SKIPPED],
        collection_errors=run.collection_errors,
        runnability=counts[TestOutcome.PASSED] / total if total else 0.0,
        duration_s=round(sum(t.duration_s for t in run.tests), 4),
        tests=run.tests,
    )


def _classify(spec: MutantSpec, run: SuiteRun, baseline_passed: list[str], applied: int) -> Mutant:
    if run.process.timed_out:
        return _mutant(spec, MutantState.TIMEOUT, run)
    if run.broken:
        return _mutant(spec, MutantState.ERROR, run)
    if applied == 0:
        return _mutant(spec, MutantState.NO_COVERAGE, run)
    outcomes = run.outcomes()
    killed_by = [node for node in baseline_passed if outcomes.get(node) in KILLING_OUTCOMES]
    state = MutantState.KILLED if killed_by else MutantState.SURVIVED
    return _mutant(spec, state, run, killed_by)


def _mutant(
    spec: MutantSpec,
    state: MutantState,
    run: SuiteRun | None = None,
    killed_by: list[str] | None = None,
) -> Mutant:
    return Mutant(
        id=spec.id,
        operator=spec.operator,
        operation=spec.ref,
        status_code=spec.status_key,
        location=spec.pointer,
        description=spec.description,
        state=state,
        killed_by=killed_by or [],
        duration_s=round(sum(t.duration_s for t in run.tests), 4) if run else None,
    )


def _summary(mutants: list[Mutant], operators: list[MutationOperator]) -> MutationSummary:
    def count(state: MutantState, of: list[Mutant] = mutants) -> int:
        return sum(m.state == state for m in of)

    def score(of: list[Mutant]) -> float:
        denominator = len(of) - count(MutantState.ERROR, of) - count(MutantState.NO_COVERAGE, of)
        killed = count(MutantState.KILLED, of) + count(MutantState.TIMEOUT, of)
        return round(killed / denominator, 4) if denominator > 0 else 0.0

    return MutationSummary(
        mutants_total=len(mutants),
        killed=count(MutantState.KILLED),
        survived=count(MutantState.SURVIVED),
        timeout=count(MutantState.TIMEOUT),
        no_coverage=count(MutantState.NO_COVERAGE),
        errors=count(MutantState.ERROR),
        mutation_score=score(mutants),
        by_operator={op: score([m for m in mutants if m.operator == op]) for op in operators},
    )


def _log_entry(mutant: Mutant, run: SuiteRun) -> str:
    output = run.process.output.decode(errors="replace")[-LOG_TAIL:]
    return f"==== {mutant.id} {mutant.state.value}: {mutant.description} ====\n{output}"

