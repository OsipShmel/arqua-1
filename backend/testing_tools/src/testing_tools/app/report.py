"""Markdown report of a finished run: what ran, what it found, the metrics."""

from collections.abc import Iterable, Sequence
from datetime import datetime

from testing_tools.api.schemas import (
    ContractCoverage,
    EvoMasterResult,
    FailureCause,
    Finding,
    MicrocksResult,
    MutantState,
    MutationResult,
    RunMetrics,
    RunResult,
    SchemathesisResult,
)

MAX_ROWS = 50
CAUSES = {
    FailureCause.SERVICE_DEFECT: "дефект сервиса",
    FailureCause.TEST_ERROR: "ошибка теста",
    FailureCause.INFRASTRUCTURE: "инфраструктура",
}


def render(result: RunResult) -> str:
    lines = [f"# Прогон {result.run_id}", ""]
    lines += _overview(result)
    lines += _metrics(result.metrics)
    lines += _tools(result)
    findings = [
        f
        for t in result.tools
        if isinstance(t, SchemathesisResult | MicrocksResult | EvoMasterResult)
        for f in t.findings
    ]
    if findings:
        lines += _findings(findings)
    for tool in result.tools:
        if isinstance(tool, SchemathesisResult | MicrocksResult | EvoMasterResult) and tool.coverage:
            lines += _coverage(tool.tool.value, tool.coverage)
        if isinstance(tool, MutationResult):
            if tool.coverage:
                lines += _coverage("тесты агента", tool.coverage)
            lines += _mutation(tool)
    return "\n".join(lines).rstrip() + "\n"


def _overview(result: RunResult) -> list[str]:
    rows = [
        ("Состояние", result.state.value),
        ("Начат", _time(result.started_at)),
        ("Завершён", _time(result.finished_at)),
        ("Длительность, с", _num(result.duration_s)),
        ("Сервис", result.target_base_url),
    ]
    if result.contract:
        rows.append(("Контракт", f"{result.contract.title} {result.contract.version} "
                                 f"(OpenAPI {result.contract.openapi_version}, "
                                 f"операций: {result.contract.operations_total})"))  # fmt: skip
        rows.append(("Источник контракта", result.contract.source or "—"))
    if result.labels:
        rows.append(("Метки", ", ".join(f"{k}={v}" for k, v in result.labels.items())))
    if result.error:
        rows.append(("Ошибка", f"{result.error.code.value}: {result.error.message}"))
    if result.environment:
        rows.append(("Окружение", ", ".join(f"{k} {v}" for k, v in result.environment.items())))
    return _table(["Прогон", ""], rows) + [""]


def _metrics(metrics: RunMetrics) -> list[str]:
    rows = [
        ("Contract Coverage (операции)", _pct(metrics.contract_coverage)),
        ("Contract Coverage (коды ответов)", _pct(metrics.status_code_coverage)),
        ("Найдено проблем", str(metrics.faults.findings_total) if metrics.faults else "—"),
        ("Покрытие операций тестами агента", _pct(metrics.tests_contract_coverage)),
        ("Покрытие кодов ответов тестами агента", _pct(metrics.tests_status_code_coverage)),
        ("Test Suite Size", _num(metrics.test_suite_size)),
        ("Runnability", _pct(metrics.runnability)),
        ("Mutation Score", _pct(metrics.mutation_score)),
    ]
    rows += [(f"Время: {tool.value}, с", _num(s)) for tool, s in metrics.duration_s.items()]
    return ["## Метрики", ""] + _table(["Метрика", "Значение"], rows) + [""]


def _tools(result: RunResult) -> list[str]:
    rows = [
        (
            t.tool.value,
            t.state.value,
            _num(t.duration_s),
            t.tool_version or "—",
            f"{t.error.code.value}: {t.error.message}" if t.error else "",
        )
        for t in result.tools
    ]
    header = ["Инструмент", "Состояние", "Время, с", "Версия", "Ошибка"]
    return ["## Инструменты", ""] + _table(header, rows) + [""]


def _findings(findings: Sequence[Finding]) -> list[str]:
    rows = [
        (
            f.severity.value,
            f.tool.value,
            f.check,
            f"{f.operation.method.value} {f.operation.path}",
            f.message.splitlines()[0] if f.message else f.title,
        )
        for f in findings[:MAX_ROWS]
    ]
    out = ["## Находки", ""]
    out += _table(["Важность", "Инструмент", "Проверка", "Операция", "Сообщение"], rows)
    if len(findings) > MAX_ROWS:
        out += ["", f"…и ещё {len(findings) - MAX_ROWS}, полный список — в result.json."]
    return out + [""]


def _coverage(tool: str, coverage: ContractCoverage) -> list[str]:
    rows = [
        (
            f"{c.operation.method.value} {c.operation.path}",
            "да" if c.tested else "нет",
            ", ".join(c.documented_status_codes),
            ", ".join(map(str, c.observed_status_codes)),
            str(c.requests),
        )
        for c in coverage.by_operation
    ]
    header = ["Операция", "Протестирована", "В контракте", "Получено", "Запросов"]
    return [f"## Покрытие контракта: {tool}", ""] + _table(header, rows) + [""]


def _mutation(result: MutationResult) -> list[str]:
    out = ["## Тесты агента и мутации", ""]
    if result.baseline:
        b = result.baseline
        out += [
            f"Baseline ({b.status.value}): {b.passed} из {b.tests_total} прошли, "
            f"упали {b.failed}, ошибок {b.errors}, пропущено {b.skipped}.",
            "",
        ]
        if b.failure_causes:
            causes = ", ".join(f"{CAUSES[c]} — {n}" for c, n in b.failure_causes.items())
            out += [f"Причины падений: {causes}.", ""]
        if b.collection_errors:
            out += ["Не собрались: " + ", ".join(f"`{e}`" for e in b.collection_errors), ""]
        rows = [
            (
                t.node_id,
                t.outcome.value,
                CAUSES[t.cause] if t.cause else "",
                (t.message or "").splitlines()[0] if t.message else "",
            )
            for t in b.tests[:MAX_ROWS]
        ]
        out += _table(["Тест", "Итог", "Причина", "Сообщение"], rows) + [""]
    if result.summary:
        s = result.summary
        out += [
            f"Мутантов {s.mutants_total}: убито {s.killed}, выжило {s.survived}, "
            f"таймаут {s.timeout}, не покрыто {s.no_coverage}, ошибок {s.errors}. "
            f"Mutation Score — {_pct(s.mutation_score)}.",
            "",
        ]
    survived = [m for m in result.mutants if m.state == MutantState.SURVIVED]
    if survived:
        mutant_rows = [
            (m.id, f"{m.operation.method.value} {m.operation.path}", m.status_code, m.description)
            for m in survived[:MAX_ROWS]
        ]
        out += ["Выжившие мутанты — поломки, которые тесты не заметили:", ""]
        out += _table(["Мутант", "Операция", "Ответ", "Что сломано"], mutant_rows) + [""]
    return out


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return out


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | int | None) -> str:
    if value is None:
        return "—"
    return str(value) if isinstance(value, int) else f"{value:.2f}"


def _time(value: datetime | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M:%S UTC")
