"""Human-readable CLI summary. Machine output remains in report.json."""

from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import TextIO


def show_input(args, output: TextIO) -> None:
    tools = ", ".join(args.tools or ["schemathesis"]) if args.tools_url else "DEMO"
    print(
        "ARQA-1 · API Quality Agent\n"
        f"  Контракт       {args.contract}\n"
        f"  API            {args.base_url}\n"
        f"  Инструменты    {tools}\n"
        f"  Лимиты         агент {args.agent_timeout:g} с, шагов {args.max_steps}, "
        f"tools {args.tools_timeout} с",
        file=output,
    )


def show_operations(summary: dict, output: TextIO) -> None:
    operations = summary.get("operations", [])
    for operation in operations[:10]:
        print(f"  {operation['method']:<7} {operation['path']}", file=output)
    if len(operations) > 10:
        print(f"  Ещё операций: {len(operations) - 10}", file=output)


def show_result(report: dict, output: TextIO) -> None:
    print("\nРезультат", file=output)
    print(f"  Вызов агента   {report['status']}", file=output)
    print(
        f"  Проверки API   {(report['execution'] or {}).get('state', 'not_run')}",
        file=output,
    )
    print(f"  Режим tools    {report['tool_mode']}", file=output)
    elapsed = datetime.fromisoformat(report["finished_at"]) - datetime.fromisoformat(
        report["started_at"]
    )
    print(f"  Время          {elapsed.total_seconds():.1f} с", file=output)

    runtime = report["runtime"] or {}
    if "llm_calls" in runtime:
        usage = (
            runtime.get("total_tokens")
            if runtime.get("usage_reports")
            else "нет данных"
        )
        print(
            f"  LLM            вызовов {runtime['llm_calls']}, токенов {usage}",
            file=output,
        )

    for run in report["tool_runs"]:
        metrics = run.get("metrics") or {}
        coverage = metrics.get("contract_coverage")
        statuses = metrics.get("status_code_coverage")
        if coverage is not None and statuses is not None:
            print(
                f"  Покрытие       операции {coverage:.1%}, коды {statuses:.1%}",
                file=output,
            )

    findings = [
        finding
        for run in report["tool_runs"]
        for tool in run.get("tools", [])
        for finding in tool.get("findings", [])
    ]
    if findings:
        print(f"\nНаходки инструментов ({len(findings)})", file=output)
        for finding in islice(findings, 5):
            operation = finding.get("operation") or {}
            request = (
                f"{operation.get('method', '')} {operation.get('path', '')}".strip()
            )
            message = finding.get("message") or finding.get("title") or ""
            first_line = message.splitlines()[0][:120] if message else ""
            print(
                f"  {finding.get('severity', ''):<8} {request}  {first_line}",
                file=output,
            )
        if len(findings) > 5:
            print(f"  Ещё находок в report.json: {len(findings) - 5}", file=output)

    generated = (report["metrics"] or {}).get("generated_pytest")
    if generated is not None:
        print(
            f"\nPytest-кейсов подготовлено: {generated['test_suite_size']}", file=output
        )
    for path in report["generated_tests"]:
        print(f"  Тесты: {path}", file=output)
    if report["artifacts"]:
        print(f"\nОтчёты: {Path(report['artifacts']['json']).parent}", file=output)
        for kind in ("json", "markdown"):
            print(f"  Отчёт ({kind}): {report['artifacts'][kind]}", file=output)
        other = [
            Path(path).name
            for kind, path in report["artifacts"].items()
            if kind not in {"json", "markdown"}
        ]
        print("  " + " · ".join(other), file=output)
