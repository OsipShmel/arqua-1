"""CLI boundary: input, one agent call, output and reports. No direct tool calls."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from agent.contracts import http_url, read_contract


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


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(
        prog="arqa-testgen",
        description="Запуск QA-агента ARQA-1 по OpenAPI-контракту.",
    )
    app.add_argument(
        "--version", action="version", version=f"%(prog)s {version('agent')}"
    )
    commands = app.add_subparsers(dest="command", required=True)
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
        "**Демонстрационный режим:** tools пока являются заглушками. "
        "Реальные тесты и фаззинг не выполнялись; pytest-код и метрики не предоставлены.\n\n"
    )
    if report.get("agent") is not None:
        text += (
            "## Ответ агента\n\n" + output_text(report["agent"].get("output")) + "\n"
        )
    if report.get("error"):
        text += "## Ошибка\n\n" + report["error"]["message"] + "\n"
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
        "schema_version": 1,
        "run_id": run_id,
        "status": "error",
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "contract": {"source": args.contract},
        "target_base_url": args.base_url,
        "tool_mode": "demo",
        "agent": None,
        "metrics": None,
        "generated_tests": [],
        "error": None,
        "artifacts": {},
    }
    code = 1
    directory_created = False
    try:
        directory.mkdir(parents=True, exist_ok=False)
        directory_created = True
        print("Читаю OpenAPI-контракт...", file=sys.stderr)
        context = read_contract(args.contract, timeout=args.contract_timeout)
        report["contract"] = context.metadata()
        print("Вызываю агент (tools в демонстрационном режиме)...", file=sys.stderr)
        # Lazy import keeps help/version/input errors independent of LLM configuration.
        from agent.agent import run_qa_agent

        with redirect_stdout(sys.stderr):
            result = run_qa_agent(context.task(args.base_url))
        if not isinstance(result, dict) or result.get("status") not in {
            "success",
            "error",
        }:
            raise ValueError("Агент вернул результат с неизвестным статусом")
        if result["status"] == "success" and "output" not in result:
            raise ValueError("В результате агента отсутствует output")
        json.dumps(result, allow_nan=False)
        report["agent"] = result
        report["status"] = result["status"]
        if result["status"] == "error":
            report["error"] = {
                "type": "AgentError",
                "message": output_text(
                    result.get("error", result.get("output", "Ошибка агента"))
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
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": error_message(exc)}

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if directory_created:
            report["artifacts"] = {
                "json": str(directory / "report.json"),
                "markdown": str(directory / "report.md"),
            }
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
        print("Режим: demo. Реальные проверки и генерация pytest пока не реализованы.")
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
    return execute(parser().parse_args(argv))
