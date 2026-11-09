"""А-2 (Ф-2, Н-1): контрольные контракты с ошибками.

Шаги: запуск с контрактами: ошибка YAML; нет раздела paths; нет HTTP-операций.
Ожидается: понятное сообщение о характере ошибки, запись в отчёт, без аварийного
завершения.
"""

from pathlib import Path

import pytest
from conftest import CliRun, cli_env, generate, scripted_llm

BROKEN = {
    "yaml_syntax": "openapi: 3.0.3\ninfo: {title: x, version: '1'\npaths: [",
    "no_paths": "openapi: 3.0.3\ninfo: {title: x, version: '1'}\n",
    "no_operations": "openapi: 3.0.3\ninfo: {title: x, version: '1'}\npaths:\n  /pets:\n    summary: text\n",
}
# What a clear message must name for each case.
HINTS = {"yaml_syntax": "yaml", "no_paths": "paths", "no_operations": "http-операций"}


def _run(case: str, workdir: Path) -> tuple[CliRun, int]:
    contract = workdir / f"{case}.yaml"
    contract.write_text(BROKEN[case], encoding="utf-8")
    with scripted_llm() as llm:
        run = generate(contract, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir)
    return run, len(llm.requests)


@pytest.mark.parametrize("case", BROKEN)
def test_broken_contract_is_reported_without_crash(case: str, workdir: Path) -> None:
    run, llm_calls = _run(case, workdir)

    assert run.code == 1
    assert "Traceback" not in run.stderr
    error = run.report["error"]
    assert error["message"]
    assert f"Ошибка: {error['message']}" in run.stderr
    assert run.report["status"] == "error"
    markdown = (run.run_dir / "report.md").read_text("utf-8")
    assert "## Ошибка" in markdown and error["message"] in markdown
    assert llm_calls == 0, "the model must not be called for a broken contract"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            "yaml_syntax",
            marks=pytest.mark.xfail(
                strict=True,
                reason="на синтаксис YAML выводится сырой текст PyYAML без слова YAML",
            ),
        ),
        "no_paths",
        "no_operations",
    ],
)
def test_message_names_the_problem(case: str, workdir: Path) -> None:
    run, _ = _run(case, workdir)

    assert HINTS[case] in run.report["error"]["message"].lower()
