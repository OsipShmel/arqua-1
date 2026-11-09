"""А-18 (Н-3): лимиты производительности.

Шаги: генерация для контракта до 20 операций; просмотр конфигурации лимитов.
Ожидается: Generation Time не более 15 мин; ожидание ответа LLM не более 60 с, не более
1 повтора; лимит runner по умолчанию 60 с; число обращений к LLM и токенов на операцию
зафиксировано, лимит задаётся пользователем.
"""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import cli_env, demo_service, generate, scripted_llm, tool_call


def contract_with(operations: int) -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Twenty", "version": "1"},
        "paths": {
            f"/items{i}": {
                "get": {
                    "operationId": f"get{i}",
                    "responses": {"200": {"description": "ok"}},
                }
            }
            for i in range(operations)
        },
    }


def _write(workdir: Path) -> Path:
    path = workdir / "twenty.json"
    path.write_text(json.dumps(contract_with(20)), encoding="utf-8")
    return path


def test_llm_wait_and_retry_defaults(workdir: Path) -> None:
    with scripted_llm() as llm:
        env = cli_env(llm)
        env.pop("DEEPCODE_TIMEOUT", None)
        run = generate(_write(workdir), "http://127.0.0.1:9", env=env, cwd=workdir)

    model = run.report["runtime"]["model"]
    assert model["timeout"] == 60.0
    assert model["max_retries"] == 1


def test_twenty_operations_are_processed(workdir: Path) -> None:
    with scripted_llm() as llm:
        started = time.monotonic()
        run = generate(
            _write(workdir), "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    assert run.code == 0
    assert run.report["contract"]["operations_total"] == 20
    assert time.monotonic() - started < 60, "the CLI itself adds no noticeable time"
    assert (
        run.report["runtime"]["llm_calls"] == 1
        and run.report["runtime"]["total_tokens"] == 120
    )


def test_user_limits_the_agent_loop(workdir: Path) -> None:
    looping = [tool_call("check_api_health", url="http://x") for _ in range(20)]
    with scripted_llm(*looping) as llm:
        run = generate(
            _write(workdir),
            "http://127.0.0.1:9",
            env=cli_env(llm),
            cwd=workdir,
            extra=("--max-steps", "5"),
        )

    assert run.code == 1
    assert run.report["error"]["type"] == "GraphRecursionError"
    assert len(llm.requests) <= 3, "five graph steps allow at most three model calls"


@pytest.mark.xfail(
    strict=True,
    reason="лимит runner по умолчанию не 60 с: CLI --tools-timeout 120, API timeout_s 1800",
)
def test_runner_default_limit_is_60_seconds() -> None:
    from agent.cli import parser
    from testing_tools.api.schemas import RunCreateRequest

    args = parser().parse_args(["generate", "c.yaml", "--base-url", "http://x"])
    assert args.tools_timeout == 60
    assert RunCreateRequest.model_fields["timeout_s"].default == 60


@pytest.mark.xfail(
    strict=True,
    reason="токены и обращения к LLM на операцию не считаются; лимита токенов нет",
)
def test_tokens_per_operation_are_recorded(workdir: Path) -> None:
    with scripted_llm() as llm:
        run = generate(
            _write(workdir), "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    assert run.report["runtime"].get("tokens_per_operation")


@pytest.mark.llm
def test_generation_time_with_real_model(workdir: Path) -> None:
    with demo_service("petstore"):
        started = time.monotonic()
        run = generate(
            _write(workdir),
            "http://127.0.0.1:9",
            env=cli_env(),
            cwd=workdir,
            extra=("--agent-timeout", "900"),
            timeout=960,
        )

    assert run.code == 0, run.report["error"]
    assert time.monotonic() - started <= 900
    assert run.report["runtime"]["llm_calls"] >= 1
