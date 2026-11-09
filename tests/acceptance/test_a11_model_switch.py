"""А-11 (Ф-11): смена модели через .env.

Шаги: сменить имя модели в .env, повторить запуск без изменения кода; просмотреть журнал
и отчёт.
Ожидается: прогон с новой моделью; зафиксированы модель, temperature, timeout,
max_retries, версия промптов, обращения к LLM, токены, версии инструментов, этапы агента,
вызовы инструментов.
"""

import json
from pathlib import Path

from conftest import (
    PETSTORE_YAML,
    PROVIDER_VARS,
    cli_env,
    demo_service,
    generate,
    scripted_llm,
    tool_call,
    tools_service,
)


def _run_with_dotenv(workdir: Path, tmp_path: Path, model: str):
    with (
        demo_service("petstore") as target,
        tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools,
    ):
        with scripted_llm(tool_call("run_api_checks", tool_name="schemathesis")) as llm:
            (workdir / ".env").write_text(
                f"DEEPCODE_API_KEY=dotenv-key\nDEEPCODE_BASE_URL={llm.base_url}\n"
                f"DEEPCODE_MODEL={model}\nDEEPCODE_TEMPERATURE=0.2\nDEEPCODE_TIMEOUT=45\n",
                encoding="utf-8",
            )
            env = {k: v for k, v in cli_env().items() if k not in PROVIDER_VARS}
            run = generate(
                PETSTORE_YAML,
                target,
                env=env,
                cwd=workdir,
                extra=("--tools-url", tools),
            )
    assert run.code == 0, run.stderr
    return run, llm


def test_model_comes_from_dotenv_without_code_changes(
    workdir: Path, tmp_path: Path
) -> None:
    first, llm_a = _run_with_dotenv(workdir, tmp_path, "model-a")
    second, llm_b = _run_with_dotenv(workdir, tmp_path, "model-b")

    assert {r["model"] for r in llm_a.requests} == {"model-a"}
    assert {r["model"] for r in llm_b.requests} == {"model-b"}
    assert first.report["runtime"]["model"]["model"] == "model-a"
    assert second.report["runtime"]["model"]["model"] == "model-b"


def test_run_records_everything_from_the_list(workdir: Path, tmp_path: Path) -> None:
    run, _ = _run_with_dotenv(workdir, tmp_path, "model-a")

    runtime = run.report["runtime"]
    assert runtime["model"] | {"base_url": None} == {
        "model": "model-a",
        "temperature": 0.2,
        "timeout": 45.0,
        "max_retries": 1,
        "base_url": None,
    }
    assert runtime["prompt"]["version"] and len(runtime["prompt"]["sha256"]) == 64
    assert runtime["llm_calls"] == 2
    assert (
        runtime["input_tokens"],
        runtime["output_tokens"],
        runtime["total_tokens"],
    ) == (200, 40, 240)
    assert runtime["usage_complete"] is True
    [tools_run] = run.report["tool_runs"]
    assert tools_run["tools"][0]["tool_version"]
    assert run.report["environment"]["packages"]
    events = [
        json.loads(line)["event"]
        for line in (run.run_dir / "events.jsonl").read_text("utf-8").splitlines()
    ]
    for stage in ("run.started", "contract.loaded", "context.prepared", "agent.started", "llm.started",
                  "gateway.started", "gateway.finished", "agent.finished", "run.finished"):  # fmt: skip
        assert stage in events, stage
    assert "dotenv-key" not in (run.run_dir / "report.json").read_text("utf-8")
