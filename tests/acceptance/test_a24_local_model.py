"""А-24 (Ф-11): локальная модель с OpenAI-совместимым API.

Шаги: указать в .env адрес и имя локальной модели с OpenAI-совместимым API, повторить
генерацию.
Ожидается: генерация выполнена локальной моделью без изменения кода; модель
зафиксирована в отчёте.

Офлайн «локальная модель» — сценарный OpenAI-совместимый сервер на 127.0.0.1. С маркером
`ollama` — настоящая модель из Ollama (`ARQA_OLLAMA_MODEL`, адрес `ARQA_OLLAMA_URL`).
"""

import os
from pathlib import Path

import pytest
from conftest import PETSTORE_YAML, PROVIDER_VARS, cli_env, generate, scripted_llm


def _dotenv_env(
    workdir: Path, base_url: str, model: str, key: str = "unused-key-9b1d"
) -> dict[str, str]:
    (workdir / ".env").write_text(
        f"DEEPCODE_API_KEY={key}\nDEEPCODE_BASE_URL={base_url}\nDEEPCODE_MODEL={model}\nDEEPCODE_TIMEOUT=300\n",
        encoding="utf-8",
    )
    return {k: v for k, v in cli_env().items() if k not in PROVIDER_VARS}


def test_local_openai_compatible_endpoint(workdir: Path) -> None:
    with scripted_llm(final="Локальная модель ответила.") as llm:
        run = generate(
            PETSTORE_YAML,
            "http://127.0.0.1:9",
            env=_dotenv_env(workdir, llm.base_url, "local-llama"),
            cwd=workdir,
        )

    assert run.code == 0, run.stderr
    assert {r["model"] for r in llm.requests} == {"local-llama"}
    assert run.report["runtime"]["model"]["model"] == "local-llama"
    assert run.report["runtime"]["model"]["base_url"] == llm.base_url
    assert run.report["agent"]["output"] == "Локальная модель ответила."


@pytest.mark.xfail(
    strict=True,
    reason="маскирование вырезает значение DEEPCODE_API_KEY из любого текста: с типичным для "
    "локальной модели ключом (local/ollama) портятся имя модели и адрес в отчёте",
)
def test_dummy_key_does_not_corrupt_the_report(workdir: Path) -> None:
    with scripted_llm() as llm:
        base_url = llm.base_url.replace("127.0.0.1", "localhost")
        run = generate(
            PETSTORE_YAML,
            "http://127.0.0.1:9",
            env=_dotenv_env(workdir, base_url, "local-llama", key="local"),
            cwd=workdir,
        )

    assert run.report["runtime"]["model"]["model"] == "local-llama"
    assert run.report["runtime"]["model"]["base_url"] == base_url


@pytest.mark.ollama
def test_ollama_model(workdir: Path) -> None:
    model = os.environ["ARQA_OLLAMA_MODEL"]
    base_url = os.environ.get("ARQA_OLLAMA_URL", "http://127.0.0.1:11434/v1")

    run = generate(
        PETSTORE_YAML, "http://127.0.0.1:9", env=_dotenv_env(workdir, base_url, model), cwd=workdir,
        extra=("--agent-timeout", "900", "--max-steps", "10"), timeout=960,
    )  # fmt: skip

    assert run.code == 0, run.report["error"]
    assert run.report["runtime"]["model"]["model"] == model
    assert run.report["runtime"]["llm_calls"] >= 1
    assert run.report["agent"]["output"]
