"""А-20 (Б-2): изоляция исполнения.

Шаги: добавить в набор контрольный тест с HTTP-запросом за пределы тестируемого сервиса;
запросить локальный файл через сервис-обёртку Schemathesis.
Ожидается: соединение не установлено, остальные тесты выполнены; доступ к файлу
отклонён; сетевая политика и лимиты заданы оркестратором.
"""

from pathlib import Path

import httpx
import pytest
from conftest import (
    PETSTORE_YAML,
    demo_service,
    inline_contract,
    inline_suite,
    static_server,
    tools_run,
    tools_service,
)


def test_test_cannot_reach_another_host(tmp_path: Path) -> None:
    hits: list[str] = []
    with static_server({"/": ("text/plain", b"secret")}) as outside:
        suite = {
            "tests/conftest.py": (
                "import os, httpx, pytest\n\n@pytest.fixture\ndef api():\n"
                "    with httpx.Client(base_url=os.environ['ARQA_BASE_URL']) as c:\n        yield c\n"
            ),
            "tests/test_suite.py": (
                "import httpx\n\n"
                "def test_service(api):\n    assert api.get('/pets').status_code == 200\n\n"
                f"def test_outside():\n    httpx.get({outside + '/'!r}, timeout=3)\n"
            ),
        }
        with demo_service("petstore") as target, tools_service(tmp_path) as tools:
            result = tools_run(
                tools,
                {
                    "contract": inline_contract(PETSTORE_YAML),
                    "target": {"base_url": target},
                    "tools": [
                        {
                            "tool": "mutation",
                            "tests": inline_suite(suite),
                            "max_mutants": 1,
                        }
                    ],
                },
            )
        hits.append(httpx.get(outside + "/").text)  # the server itself was up

    tests = {
        t["node_id"].split("::")[-1]: t for t in result["tools"][0]["baseline"]["tests"]
    }
    assert tests["test_service"]["outcome"] == "passed", "the rest of the suite runs"
    assert tests["test_outside"]["outcome"] == "failed"
    assert "is blocked" in tests["test_outside"]["message"]
    assert hits == ["secret"]


@pytest.mark.parametrize(
    "field",
    [
        {"contract": {"kind": "url", "url": "file:///etc/passwd"}},
        {"target": {"base_url": "file:///etc/passwd"}},
    ],
)
def test_local_file_through_the_wrapper_is_refused(field: dict, tmp_path: Path) -> None:
    payload = {
        "contract": inline_contract(PETSTORE_YAML),
        "target": {"base_url": "http://127.0.0.1:9"},
        "tools": [{"tool": "schemathesis"}],
    } | field
    with tools_service(tmp_path) as tools:
        response = httpx.post(f"{tools}/runs", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "root:" not in response.text


def test_policy_and_limits_come_from_the_orchestrator() -> None:
    from testing_tools.api.schemas import MutationConfig, RunCreateRequest, Target
    from testing_tools.app.settings import Settings

    assert Settings(_env_file=None).isolate_test_network is True
    assert RunCreateRequest.model_fields["timeout_s"].metadata, "run time is bounded"
    assert MutationConfig.model_fields["per_mutant_timeout_s"].default > 0
    assert Target.model_fields["request_timeout_s"].default > 0
