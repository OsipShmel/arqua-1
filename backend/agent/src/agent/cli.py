"""CLI boundary: input, one agent call, output and reports. No direct tool calls."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from agent.contracts import http_url, read_contract
from agent.events import Journal, atomic_json, redact
from agent.manifest import environment_manifest
from agent.results import AgentResult, save_result_files
from agent.runner import invoke_agent
from agent.source_context import read_source_context


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Нужно положительное конечное число секунд"
        ) from exc
    return seconds


def base_url(value: str) -> str:
    try:
        return http_url(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def positive_integer(value: str) -> int:
    try:
        result = int(value)
        if result < 1:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Нужно положительное целое число") from exc
    return result


def tools_seconds(value: str) -> int:
    result = positive_integer(value)
    if not 10 <= result <= 86_400:
        raise argparse.ArgumentTypeError(
            "Таймаут tools должен быть от 10 до 86400 секунд"
        )
    return result


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(
        prog="arqa-testgen",
        description="Запуск QA-агента ARQA-1 по OpenAPI-контракту.",
    )
    app.add_argument(
        "--version", action="version", version=f"%(prog)s {version('agent')}"
    )
    commands = app.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Проверить окружение и настройки без запросов к LLM"
    )
    doctor.add_argument("--format", choices=("text", "json"), default="text")
    generate = commands.add_parser("generate", help="Передать контракт QA-агенту")
    generate.add_argument(
        "contract", help="Путь или HTTP(S) URL к OpenAPI 3.x JSON/YAML"
    )
    generate.add_argument(
        "--base-url", required=True, type=base_url, help="Адрес тестируемого API"
    )
    generate.add_argument(
        "--output", type=Path, default=Path("runs"), help="Каталог прогонов (runs)"
    )
    generate.add_argument(
        "--format", choices=("text", "json"), default="text", help="Формат stdout"
    )
    generate.add_argument(
        "--contract-timeout",
        type=positive_seconds,
        default=10.0,
        help="Таймаут загрузки контракта по URL, секунд (10)",
    )
    generate.add_argument(
        "--agent-timeout",
        type=positive_seconds,
        default=300.0,
        help="Максимальное время процесса агента, секунд (300)",
    )
    generate.add_argument(
        "--max-steps",
        type=positive_integer,
        default=25,
        help="Максимальное число шагов графа агента (25)",
    )
    generate.add_argument(
        "--source-dir", type=Path, help="Опциональный каталог Python/FastAPI-исходников"
    )
    generate.add_argument(
        "--source-max-chars",
        type=positive_integer,
        default=24_000,
        help="Бюджет контекста исходников в символах (24000)",
    )
    generate.add_argument(
        "--summary-max-chars",
        type=positive_integer,
        default=100_000,
        help="Максимальный размер summary в символах (100000)",
    )
    generate.add_argument(
        "--tools-url",
        type=base_url,
        help="Адрес tools API, включая /api/v1; обращения выполняет агент",
    )
    generate.add_argument(
        "--tools",
        nargs="+",
        choices=("schemathesis", "microcks"),
        help="Разрешённые агенту инструменты; по умолчанию schemathesis при --tools-url",
    )
    generate.add_argument(
        "--tools-timeout",
        type=tools_seconds,
        default=120,
        help="Срок прогона tools, секунд (120)",
    )
    return app


def output_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        if text_blocks:
            return "\n".join(text_blocks)
    return json.dumps(content, ensure_ascii=False, indent=2)


def error_message(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    key = os.getenv("DEEPCODE_API_KEY")
    if key:
        message = message.replace(key, "***")
    return message


def write_reports(directory: Path, report: dict) -> None:
    """Publish each file atomically; retain separate directories for every attempt."""
    text = (
        "# Прогон QA-агента ARQA-1\n\n"
        f"- Run ID: {report['run_id']}\n- Статус вызова: {report['status']}\n"
        f"- Начало (UTC): {report['started_at']}\n"
        f"- Окончание (UTC): {report['finished_at']}\n"
        f"- Сервис: {report['target_base_url']}\n\n"
        f"- Таймаут агента: {report['limits']['agent_timeout']:g} секунд\n"
        f"- Предел шагов графа: {report['limits']['max_steps']}\n\n"
    )
    text += f"- Режим tools: {report['tool_mode']}\n"
    if report.get("execution"):
        text += f"- Итог проверок: {report['execution'].get('state', 'unknown')}\n"
    contract = report["contract"]
    if "title" in contract:
        text += f"- Контракт: {contract['title']} {contract['api_version']}; операций: {contract['operations_total']}\n"
    if report["tool_mode"] == "demo":
        text += "\n**DEMO:** инструменты-заглушки не выполняют проверки API или генерацию pytest.\n"
    elif report["tool_mode"] == "external":
        text += "\nИспользован внешний tools API. Его real/stub-режим настраивается на сервере.\n"
    else:
        text += "\nФактический режим инструментов указан агентом; успешный вызов не означает прохождение тестов.\n"
    if report.get("source"):
        text += f"\nИсходники: {report['source']['files_total']} файлов, контекст ограничен бюджетом.\n"
    if report.get("runtime"):
        runtime = report["runtime"]
        text += f"\nLLM-вызовов: {runtime.get('llm_calls', 0)}; сообщений с usage: {runtime.get('usage_reports', 0)}.\n"
        if runtime.get("usage_reports"):
            text += f"Зафиксировано токенов: {runtime.get('total_tokens')}; полный учёт: {runtime.get('usage_complete')}.\n"
        else:
            text += "Данные о расходе токенов от провайдера не получены.\n"
    if report.get("metrics") is not None:
        text += (
            "\n## Метрики инструментов\n\n```json\n"
            + json.dumps(report["metrics"], ensure_ascii=False, indent=2)
            + "\n```\n"
        )
    if report.get("generated_tests"):
        text += (
            "\n## Полученные pytest-файлы\n\n"
            + "\n".join(f"- {p}" for p in report["generated_tests"])
            + "\n"
        )
    if report.get("agent") is not None:
        text += (
            "## Ответ агента\n\n" + output_text(report["agent"].get("output")) + "\n"
        )
    if report.get("error"):
        text += "## Ошибка\n\n" + report["error"]["message"] + "\n"
    atomic_json(
        directory / "manifest.json",
        {
            "schema_version": 1,
            "run_id": report["run_id"],
            "status": report["status"],
            "started_at": report["started_at"],
            "finished_at": report["finished_at"],
            "contract": report["contract"],
            "source": report["source"],
            "parameters": report["parameters"],
            "environment": report["environment"],
            "runtime": report["runtime"],
            "files": report["files"],
            "tool_artifacts": report["tool_artifacts"],
            "tool_runs": [
                {"run_id": r.get("run_id"), "state": r.get("state")}
                for r in report["tool_runs"]
            ],
        },
    )
    for name, content in (
        (
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        ),
        ("report.md", text),
    ):
        temporary = directory / f".{name}.tmp"
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(directory / name)


def execute(args: argparse.Namespace) -> int:
    started_at = datetime.now(timezone.utc)
    run_id = f"{started_at:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
    directory = args.output.expanduser().resolve() / run_id
    report = {
        "schema_version": 2,
        "run_id": run_id,
        "status": "error",
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "contract": {"source": args.contract},
        "target_base_url": args.base_url,
        "tool_mode": "unknown",
        "agent": None,
        "metrics": None,
        "generated_tests": [],
        "error": None,
        "artifacts": {},
        "limits": {"agent_timeout": args.agent_timeout, "max_steps": args.max_steps},
        "source": None,
        "runtime": None,
        "environment": None,
        "execution": None,
        "tool_runs": [],
        "tool_artifacts": [],
        "files": [],
        "parameters": {
            k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
        },
    }
    code = 1
    directory_created = False
    journal = Journal(None, run_id)
    try:
        directory.mkdir(parents=True, exist_ok=False)
        directory_created = True
        journal = Journal(directory / "events.jsonl", run_id)
        journal.emit("run.started")
        report["environment"] = environment_manifest()
        if args.tools and not args.tools_url:
            raise ValueError("Для --tools необходимо указать --tools-url")
        print("Читаю OpenAPI-контракт...", file=sys.stderr)
        context = read_contract(args.contract, timeout=args.contract_timeout)
        report["contract"] = context.metadata()
        journal.emit("contract.loaded", **context.metadata())
        summary = context.summary(args.summary_max_chars)
        atomic_json(directory / "contract-summary.json", summary)
        print(
            f"Контракт: {context.contract.title}, OpenAPI {context.contract.openapi_version}; операций: {len(context.contract.operations)}",
            file=sys.stderr,
        )
        journal.emit(
            "context.prepared",
            operations=len(summary["operations"]),
            summary_chars=len(json.dumps(summary, ensure_ascii=False)),
        )
        source = None
        if args.source_dir:
            source = read_source_context(args.source_dir, args.source_max_chars)
            report["source"] = {
                "root": source["root"],
                "files_total": len(source["files"]),
                "files": [
                    {"path": f["path"], "sha256": f["sha256"]} for f in source["files"]
                ],
                "skipped": source["skipped"],
                "truncated": source["truncated"],
                "max_chars": source["max_chars"],
            }
            atomic_json(directory / "source-context.json", source)
            journal.emit(
                "source.prepared",
                files=len(source["files"]),
                truncated=source["truncated"],
            )
        agent_context = {
            "run_id": run_id,
            "events_path": str(directory / "events.jsonl"),
            "runtime_path": str(directory / "runtime.json"),
        }
        if args.tools_url:
            agent_context["gateway"] = {
                "url": args.tools_url,
                "target": args.base_url,
                "contract": context.contract.raw,
                "directory": str(directory),
                "tools": list(dict.fromkeys(args.tools or ["schemathesis"])),
                "timeout": args.tools_timeout,
            }
        print(
            "Вызываю агент (tools API)..."
            if args.tools_url
            else "Вызываю агент (tools в демонстрационном режиме)...",
            file=sys.stderr,
        )
        result = invoke_agent(
            context.task(
                args.base_url, source_context=source, max_chars=args.summary_max_chars
            ),
            timeout=args.agent_timeout,
            max_steps=args.max_steps,
            context=agent_context,
        )
        json.dumps(result, allow_nan=False)
        validated = AgentResult.model_validate(redact(result))
        report["files"] = save_result_files(directory, validated.files)
        report["generated_tests"] = [
            f["path"] for f in report["files"] if f["kind"] == "pytest"
        ]
        result = validated.model_dump(exclude={"files"})
        report["agent"] = result
        for field in (
            "tool_mode",
            "metrics",
            "execution",
            "runtime",
            "tool_runs",
            "tool_artifacts",
        ):
            report[field] = result[field]
        report["status"] = result["status"]
        if result["status"] == "error":
            report["error"] = {
                "type": result.get("error_type") or "AgentError",
                "message": error_message(
                    RuntimeError(
                        output_text(
                            result.get("error")
                            or result.get("output")
                            or "Ошибка агента"
                        )
                    )
                ),
            }
        code = 0 if result["status"] == "success" else 1
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        report["error"] = {
            "type": "KeyboardInterrupt",
            "message": "Запуск прерван пользователем",
        }
        code = 130
    except TimeoutError as exc:
        report["status"] = "timeout"
        report["error"] = {"type": "TimeoutError", "message": error_message(exc)}
        code = 124
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": error_message(exc)}

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if directory_created:
            runtime_path = directory / "runtime.json"
            if report["runtime"] is None and runtime_path.is_file():
                report["runtime"] = json.loads(runtime_path.read_text(encoding="utf-8"))
            journal.emit("run.finished", status=report["status"], error=report["error"])
            report["artifacts"] = {
                "json": str(directory / "report.json"),
                "markdown": str(directory / "report.md"),
                "manifest": str(directory / "manifest.json"),
                "events": str(directory / "events.jsonl"),
            }
            for name, filename in (
                ("summary", "contract-summary.json"),
                ("source_context", "source-context.json"),
                ("runtime", "runtime.json"),
            ):
                if (directory / filename).is_file():
                    report["artifacts"][name] = str(directory / filename)
            write_reports(directory, report)
    except OSError as exc:
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__, "message": error_message(exc)}
        report["artifacts"] = {}
        code = 1

    if report["error"]:
        print(f"Ошибка: {report['error']['message']}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        if report["agent"] is not None:
            print(output_text(report["agent"].get("output")))
        print(
            f"Режим tools: {report['tool_mode']}. Успешный вызов агента не означает прохождение тестов."
        )
        for kind, path in report["artifacts"].items():
            print(f"Отчёт ({kind}): {path}")
    return code


def main(argv: list[str] | None = None) -> int:
    # Windows pipes otherwise use the system code page, breaking UTF-8 JSON.
    for stream in (sys.stdout, sys.stderr):
        if (
            not stream.isatty()
            and hasattr(stream, "reconfigure")
            and (stream.encoding or "").lower().replace("-", "") != "utf8"
        ):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args(argv)
    if args.command == "doctor":
        from agent.doctor import diagnose

        report = diagnose()
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for check in report["checks"]:
                print(f"[{check['status']}] {check['name']}: {check['message']}")
            print(
                "Проверка локальная; доступность LLM и поддержка tools не проверялись."
            )
        return 0 if report["status"] == "ok" else 1
    return execute(args)
