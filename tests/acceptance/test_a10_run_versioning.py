"""А-10 (Ф-10): версионирование прогонов.

Шаги: два последовательных запуска на одном контракте.
Ожидается: два каталога с датой и временем; первый не изменён; отчёты Markdown, JSON; в
тестах — версия контракта, модель, время генерации.
"""

import hashlib
import re
from pathlib import Path

import pytest
from conftest import PETSTORE_YAML, cli_env, generate, generated_suite, scripted_llm

RUN_DIR = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_two_runs_two_directories_first_untouched(workdir: Path) -> None:
    with scripted_llm(final="первый") as llm:
        first = generate(
            PETSTORE_YAML, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )
    before = _snapshot(first.run_dir)
    with scripted_llm(final="второй") as llm:
        second = generate(
            PETSTORE_YAML, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    assert first.code == second.code == 0
    assert first.run_dir != second.run_dir
    assert first.run_dir.parent == second.run_dir.parent
    assert RUN_DIR.match(first.run_dir.name) and RUN_DIR.match(second.run_dir.name)
    assert first.run_dir.name < second.run_dir.name, "names sort by start time"
    assert _snapshot(first.run_dir) == before
    for run in (first, second):
        assert {"report.json", "report.md", "manifest.json", "events.jsonl"} <= set(
            _snapshot(run.run_dir)
        )


def test_manifest_records_contract_and_model(workdir: Path) -> None:
    with scripted_llm() as llm:
        run = generate(
            PETSTORE_YAML, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    manifest = (run.run_dir / "manifest.json").read_text("utf-8")
    assert '"api_version": "1.0.0"' in manifest
    assert '"sha256"' in manifest
    assert '"scripted-model"' in manifest
    assert run.report["started_at"] and run.report["finished_at"]


@pytest.mark.xfail(strict=True, reason="тестов нет (А-4), метаданных в них тоже")
def test_generated_tests_carry_contract_version_model_and_time(workdir: Path) -> None:
    with scripted_llm() as llm:
        run = generate(
            PETSTORE_YAML, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir
        )

    [first, *_] = generated_suite(run)
    header = first.read_text("utf-8")[:2000]
    assert (
        "1.0.0" in header
        and "scripted-model" in header
        and run.report["started_at"][:10] in header
    )
