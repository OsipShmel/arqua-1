"""А-17 (Н-2): превышение лимита времени.

Шаги: лимит времени runner меньше времени выполнения набора; затем повторный запуск
только этапа выполнения тестов.
Ожидается: частичные результаты сохранены, статус «превышение времени»; выдуманных данных
нет, сбой в отчёте; этап выполнен без повторной генерации.
"""

import json
from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    artifact,
    cli_env,
    demo_service,
    generate,
    inline_contract,
    inline_suite,
    run_cli,
    scripted_llm,
    tools_run,
    tools_service,
)

SLOW_SUITE = {
    "tests/test_slow.py": "import time\n\ndef test_slow():\n    time.sleep(60)\n",
}


def test_runner_timeout_keeps_partial_results(tmp_path: Path) -> None:
    with demo_service("petstore") as target, tools_service(tmp_path) as tools:
        result = tools_run(
            tools,
            {
                "contract": inline_contract(PETSTORE_YAML),
                "target": {"base_url": target},
                "tools": [
                    {
                        "tool": "schemathesis",
                        "max_examples": 3,
                        "seed": 1,
                        "phases": ["examples"],
                    },
                    {"tool": "mutation", "tests": inline_suite(SLOW_SUITE)},
                ],
                "timeout_s": 10,
            },
        )
        report = artifact(tools, result, "run-report.md").decode()

    assert result["state"] == "timed_out"
    assert result["error"]["code"] == "timeout"
    schemathesis, mutation = result["tools"]
    assert schemathesis["state"] in {"passed", "failed"} and schemathesis["summary"], (
        "finished part is kept"
    )
    assert mutation["state"] == "error" and mutation["error"]["code"] == "timeout"
    assert mutation["baseline"] is None and mutation["summary"] is None, (
        "nothing is made up"
    )
    assert "timed_out" in report and "timeout" in report
    [saved] = (tmp_path / "tt-results").glob(f"*_{result['run_id']}/result.json")
    assert json.loads(saved.read_text("utf-8"))["state"] == "timed_out"


def test_agent_timeout_keeps_partial_run(workdir: Path) -> None:
    with scripted_llm(delay=30) as llm:
        run = generate(
            PETSTORE_YAML,
            "http://127.0.0.1:9",
            env=cli_env(llm),
            cwd=workdir,
            extra=("--agent-timeout", "5"),
        )

    assert run.code == 124
    assert run.report["status"] == "timeout"
    assert "--agent-timeout" in run.report["error"]["message"]
    assert run.report["runtime"]["llm_calls"] == 1, (
        "partial telemetry survives the kill"
    )
    assert run.report["agent"] is None
    assert (run.run_dir / "contract-summary.json").is_file()


@pytest.mark.xfail(
    strict=True, reason="нет команды повторного запуска только этапа выполнения тестов"
)
def test_rerun_only_the_execution_stage(workdir: Path) -> None:
    help_text = run_cli("--help", env=cli_env(), cwd=workdir).stdout

    assert any(command in help_text for command in ("run-tests", "execute", "rerun"))
