"""Local classroom demonstration of the existing ARQA CLI, without paid LLM calls."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Локальный показ CLI: настоящие tools, имитатор LLM"
    )
    parser.add_argument(
        "--output", type=Path, help="Каталог демонстраций (по умолчанию runs)"
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.environ.update(
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        NO_PROXY="127.0.0.1,localhost",
    )
    repo = Path(__file__).resolve().parents[3]
    # Reuse the tested local servers from the repository; install all workspace extras.
    sys.path.insert(0, str(repo / "backend" / "agent" / "tests"))
    from test_cli_integration import SPEC, isolated_env, scripted_provider
    from test_workspace_integration import broken_api, gateway_server

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stand = (args.output or (repo / "runs")).resolve() / ("teacher-demo-" + stamp)
    stand.mkdir(parents=True)
    spec = json.loads(json.dumps(SPEC))
    spec["info"]["title"] = "CLI classroom demo"
    spec["paths"]["/pets"]["get"]["responses"]["200"]["content"] = {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            }
        }
    }
    contract = stand / "openapi.json"
    contract.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("ARQA-1: демонстрация консольного интерфейса", flush=True)
    print("CLI, LangChain, tools API и Schemathesis настоящие.", flush=True)
    print(
        "LLM — локальный имитатор: выбор инструмента задан сценарием, ключ не нужен.",
        flush=True,
    )
    print(
        "Дефект стенда: контракт требует integer id, API возвращает строку.\n",
        flush=True,
    )
    with (
        gateway_server(stand) as tools_url,
        broken_api() as target_url,
        scripted_provider("gateway") as (model_url, _),
    ):
        env = isolated_env()
        env.update(
            DEEPCODE_API_KEY="local-test-key",
            DEEPCODE_BASE_URL=model_url + "/v1",
            DEEPCODE_MODEL="scripted-local-provider",
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "agent",
            "generate",
            str(contract),
            "--base-url",
            target_url,
            "--tools-url",
            tools_url,
            "--tools",
            "schemathesis",
            "--tools-timeout",
            "60",
            "--agent-timeout",
            "90",
            "--output",
            str(stand / "reports"),
        ]
        print("Запускается существующая команда CLI:", flush=True)
        display = (
            subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        )
        print(display + "\n", flush=True)
        result = subprocess.run(command, cwd=repo, env=env, timeout=100)
    if result.returncode:
        return result.returncode
    reports = list((stand / "reports").glob("*/report.json"))
    if len(reports) != 1:
        raise RuntimeError("Не найден единственный отчёт демонстрации")
    report_path = reports[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = [
        f
        for r in report.get("tool_runs", [])
        for t in r.get("tools", [])
        for f in t.get("findings", [])
    ]
    if not any(f.get("check") == "response_schema_conformance" for f in findings):
        raise RuntimeError(
            "Schemathesis не подтвердил ожидаемый дефект стенда; проверьте report.json"
        )
    if not any(
        Path(a["path"]).suffix == ".xml" for a in report.get("tool_artifacts", [])
    ):
        raise RuntimeError("Не сохранён ожидаемый JUnit; проверьте report.json")
    print("\nЧто показать преподавателю:")
    print("Контракт:", contract)
    print("Отчёт Markdown:", report_path.with_suffix(".md"))
    print("Отчёт JSON:", report_path)
    print("Журнал:", report_path.parent / "events.jsonl")
    print("Статус вызова агента:", report["status"])
    print("Результат проверок API:", report["execution"]["state"])
    for run in report.get("tool_runs", []):
        for tool in run.get("tools", []):
            for finding in tool.get("findings", []):
                if finding.get("check") == "response_schema_conformance":
                    print(
                        "Найден дефект:", finding.get("message", finding.get("title"))
                    )
    for artifact in report.get("tool_artifacts", []):
        if Path(artifact["path"]).suffix == ".xml":
            print("JUnit:", artifact["path"])
    print("\nsuccess: CLI успешно получил ответ агента; failed: обнаружен дефект API.")
    print(
        "Серверы остановлены. Файлы демонстрации сохранены, повторный запуск создаст новый каталог."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
