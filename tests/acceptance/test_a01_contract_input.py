"""А-1 (Ф-1, П-1): чтение контракта из файла YAML и по URL в JSON.

Шаги: запуск CLI с контрактом демо-сервиса в YAML по локальному пути; тот же контракт
в JSON по URL.
Ожидается: контракт прочитан; выведены тип спецификации, название и версия API, число
и перечень операций; сведения записаны в отчёт прогона.
"""

import json
from pathlib import Path

import pytest
import yaml
from conftest import PETSTORE_YAML, cli_env, generate, scripted_llm, static_server

OPERATIONS = ["GET /pets", "POST /pets", "GET /pets/{petId}", "DELETE /pets/{petId}"]


def _run(contract: str, workdir: Path):
    with scripted_llm(final="Контракт изучен.") as llm:
        return generate(contract, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir)


@pytest.fixture
def json_url():
    body = json.dumps(yaml.safe_load(PETSTORE_YAML.read_text())).encode()
    with static_server({"/openapi.json": ("application/json", body)}) as url:
        yield url + "/openapi.json"


def _check_metadata(run, input_format: str) -> None:
    assert run.code == 0, run.stderr
    assert "Контракт: Demo Petstore, OpenAPI 3.0.3; операций: 4" in run.stderr
    contract = run.report["contract"]
    assert contract["title"] == "Demo Petstore"
    assert contract["api_version"] == "1.0.0"
    assert contract["openapi_version"] == "3.0.3"
    assert contract["operations_total"] == 4
    assert contract["input_format"] == input_format
    saved = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["contract"] == contract
    assert "Demo Petstore 1.0.0; операций: 4" in (run.run_dir / "report.md").read_text(
        "utf-8"
    )


def test_yaml_contract_from_local_path(workdir: Path) -> None:
    _check_metadata(_run(str(PETSTORE_YAML), workdir), "yaml")


def test_json_contract_from_url(workdir: Path, json_url: str) -> None:
    run = _run(json_url, workdir)

    _check_metadata(run, "json")
    assert run.report["contract"]["source"] == json_url


def test_yaml_and_json_give_the_same_contract(workdir: Path, json_url: str) -> None:
    from_file = _run(str(PETSTORE_YAML), workdir).run_dir / "contract-summary.json"
    from_url = _run(json_url, workdir).run_dir / "contract-summary.json"

    assert json.loads(from_file.read_text("utf-8")) == json.loads(
        from_url.read_text("utf-8")
    )


def test_operations_are_summarized_for_the_run(workdir: Path) -> None:
    run = _run(str(PETSTORE_YAML), workdir)

    summary = json.loads((run.run_dir / "contract-summary.json").read_text("utf-8"))
    listed = [f"{op['method'].upper()} {op['path']}" for op in summary["operations"]]
    assert sorted(listed) == sorted(OPERATIONS)


@pytest.mark.xfail(
    strict=True, reason="перечень операций не выводится и не пишется в report.md"
)
def test_operation_list_is_printed_and_in_report(workdir: Path) -> None:
    run = _run(str(PETSTORE_YAML), workdir)

    markdown = (run.run_dir / "report.md").read_text("utf-8")
    for operation in OPERATIONS:
        assert operation in run.stderr
        assert operation in markdown
