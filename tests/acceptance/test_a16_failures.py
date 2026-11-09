"""А-16 (Н-1): отказы окружения.

Шаги: генерация при остановленном контейнере сервиса; при неверном адресе LLM в .env.
Ожидается: понятное сообщение, запись в отчёт, без аварийного завершения; таймаут и
повтор по таблице 2 ТЗ.

Таблицы 2 нет; проверяются значения по умолчанию из ПМИ А-18: ожидание LLM 60 с, один
повтор.
"""

import time
from pathlib import Path

import pytest
from conftest import (
    PETSTORE_YAML,
    PROVIDER_VARS,
    cli_env,
    free_port,
    generate,
    scripted_llm,
    tool_call,
    tools_service,
)


def _llm_env(workdir: Path, base_url: str, timeout: str = "60") -> dict[str, str]:
    (workdir / ".env").write_text(
        f"DEEPCODE_API_KEY=k\nDEEPCODE_BASE_URL={base_url}\nDEEPCODE_MODEL=m\nDEEPCODE_TIMEOUT={timeout}\n",
        encoding="utf-8",
    )
    return {k: v for k, v in cli_env().items() if k not in PROVIDER_VARS}


def _assert_reported_failure(run, code: int = 1) -> None:
    assert run.code == code, run.stderr
    assert "Traceback" not in run.stderr
    assert run.report["status"] in {"error", "timeout"}
    assert run.report["error"]["message"]
    assert (run.run_dir / "report.md").read_text("utf-8").count("## Ошибка") == 1


def test_stopped_service_is_reported(workdir: Path, tmp_path: Path) -> None:
    stopped = f"http://127.0.0.1:{free_port()}"
    with tools_service(tmp_path) as tools:
        with scripted_llm(
            tool_call("run_api_checks", tool_name="schemathesis"),
            final="Сервис недоступен.",
        ) as llm:
            run = generate(
                PETSTORE_YAML,
                stopped,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools),
            )

    assert run.code == 0, run.stderr
    assert run.report["execution"]["state"] == "error"
    assert "target_unreachable" in llm.tool_results()[0], "the model is told why"
    assert (run.run_dir / "report.md").is_file()


def test_unreachable_llm_address(workdir: Path) -> None:
    env = _llm_env(workdir, f"http://127.0.0.1:{free_port()}/v1")

    run = generate(
        PETSTORE_YAML, "http://127.0.0.1:9", env=env, cwd=workdir, timeout=120
    )

    _assert_reported_failure(run)
    assert run.report["error"]["type"] == "OpenAIConnectionError"


@pytest.mark.xfail(
    strict=True,
    reason="сообщение — голое «Connection error.»: не сказано, что недоступна LLM и по какому адресу",
)
def test_unreachable_llm_message_is_clear(workdir: Path) -> None:
    address = f"http://127.0.0.1:{free_port()}/v1"
    run = generate(
        PETSTORE_YAML, "http://127.0.0.1:9", env=_llm_env(workdir, address), cwd=workdir
    )

    message = run.report["error"]["message"]
    assert "LLM" in message or "модел" in message
    assert address in message


def test_llm_error_is_retried_once(workdir: Path) -> None:
    with scripted_llm(status=500) as llm:
        env = _llm_env(workdir, llm.base_url)
        run = generate(PETSTORE_YAML, "http://127.0.0.1:9", env=env, cwd=workdir)

    _assert_reported_failure(run)
    assert len(llm.requests) == 2, "one request and exactly one retry"


def test_llm_timeout_is_applied(workdir: Path) -> None:
    with scripted_llm(delay=5) as llm:
        env = _llm_env(workdir, llm.base_url, timeout="1")
        started = time.monotonic()
        run = generate(PETSTORE_YAML, "http://127.0.0.1:9", env=env, cwd=workdir)
        elapsed = time.monotonic() - started

    _assert_reported_failure(run)
    assert run.report["error"]["type"] == "OpenAITimeoutError"
    assert len(llm.requests) == 2
    assert elapsed < 30
