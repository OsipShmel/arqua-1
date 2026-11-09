"""А-5 (Ф-5): сценарные тесты.

Шаги: генерация сценарных тестов для сервиса со связанными операциями (создание и
чтение ресурса).
Ожидается: тест — цепочка операций с ожидаемым результатом каждого шага и конечной целью.

Зависит от генератора (А-4), которого нет.
"""

from pathlib import Path

import pytest
from conftest import (
    ORDERS_YAML,
    cli_env,
    demo_service,
    generate,
    generated_suite,
    scripted_llm,
)

GAP = "генератора pytest нет; сценарных цепочек нет"


def _chains(run) -> list[str]:
    """Test files that both create an order and read it back."""
    return [
        text
        for text in (f.read_text("utf-8") for f in generated_suite(run))
        if 'post("/orders' in text.replace("'", '"')
        and 'get(f"/orders/' in text.replace("'", '"')
    ]


@pytest.mark.xfail(strict=True, reason=GAP)
def test_create_then_read_chain(workdir: Path) -> None:
    with demo_service("orders") as target, scripted_llm() as llm:
        run = generate(ORDERS_YAML, target, env=cli_env(llm), cwd=workdir)

    assert _chains(run)


@pytest.mark.llm
@pytest.mark.xfail(strict=True, reason=GAP)
def test_create_then_read_chain_by_real_model(workdir: Path) -> None:
    with demo_service("orders") as target:
        run = generate(
            ORDERS_YAML,
            target,
            env=cli_env(),
            cwd=workdir,
            extra=("--max-steps", "40"),
            timeout=900,
        )

    assert _chains(run)
