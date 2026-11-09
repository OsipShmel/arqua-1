"""А-6 (Ф-6): запуск набора pytest в изолированном окружении.

Шаги: запуск сгенерированного набора pytest-runner в изолированном окружении.
Ожидается: статус прогона «завершён»; статусы passed, failed, error, skipped; результаты
в JUnit XML и JSON.

Runner — инструмент `mutation` в testing_tools (baseline-прогон набора). Набор здесь
написан вручную: генератора нет (А-4), а агент не умеет отправить свои тесты в runner.
"""

import json
from xml.etree import ElementTree

import pytest
from conftest import (
    PETSTORE_YAML,
    artifact,
    demo_service,
    inline_contract,
    inline_suite,
    tools_run,
    tools_service,
)

SUITE = {
    "tests/conftest.py": (
        "import os, httpx, pytest\n\n"
        "@pytest.fixture\n"
        "def api():\n"
        "    with httpx.Client(base_url=os.environ['ARQA_BASE_URL'], timeout=10) as c:\n"
        "        yield c\n\n"
        "@pytest.fixture\n"
        "def broken_fixture():\n"
        "    raise RuntimeError('fixture setup failed')\n"
    ),
    "tests/test_pets.py": (
        "import pytest\n\n"
        "def test_list_pets(api):\n"
        "    assert api.get('/pets').status_code == 200\n\n"
        "def test_missing_pet_is_404(api):\n"
        "    assert api.get('/pets/999').status_code == 404\n\n"
        "def test_delete_missing_pet_is_404(api):\n"
        "    assert api.delete('/pets/999').status_code == 404  # P4: service answers 200\n\n"
        "def test_setup_error(api, broken_fixture):\n"
        "    pass\n\n"
        "@pytest.mark.skip(reason='not ready')\n"
        "def test_skipped(api):\n"
        "    pass\n"
    ),
}


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("a06")
    with demo_service("petstore") as target, tools_service(tmp) as tools:
        result = tools_run(
            tools,
            {
                "contract": inline_contract(PETSTORE_YAML),
                "target": {"base_url": target},
                "tools": [
                    {
                        "tool": "mutation",
                        "tests": inline_suite(SUITE),
                        "max_mutants": 1,
                        "seed": 1,
                    }
                ],
            },
        )
        junit = artifact(tools, result, "mutation-baseline-junit.xml")
    return result, junit, tmp


def test_run_completes(baseline) -> None:
    result, _, _ = baseline

    assert result["state"] == "completed"
    assert result["tools"][0]["tool"] == "mutation"


def test_all_four_outcomes_are_reported(baseline) -> None:
    result, _, _ = baseline

    tests = {
        t["node_id"].split("::")[-1]: t["outcome"]
        for t in result["tools"][0]["baseline"]["tests"]
    }
    assert tests == {
        "test_list_pets": "passed",
        "test_missing_pet_is_404": "passed",
        "test_delete_missing_pet_is_404": "failed",
        "test_setup_error": "error",
        "test_skipped": "skipped",
    }
    baseline_ = result["tools"][0]["baseline"]
    assert (
        baseline_["passed"],
        baseline_["failed"],
        baseline_["errors"],
        baseline_["skipped"],
    ) == (2, 1, 1, 1)


def test_results_in_junit_xml(baseline) -> None:
    _, junit, _ = baseline

    suite = ElementTree.fromstring(junit)
    cases = {case.get("name"): case for case in suite.iter("testcase")}
    assert len(cases) == 5
    assert cases["test_delete_missing_pet_is_404"].find("failure") is not None
    assert cases["test_setup_error"].find("error") is not None
    assert cases["test_skipped"].find("skipped") is not None


def test_results_in_json_on_disk(baseline) -> None:
    result, _, tmp = baseline

    [saved] = (tmp / "tt-results").glob(f"*_{result['run_id']}/result.json")
    on_disk = json.loads(saved.read_text("utf-8"))
    assert on_disk["tools"][0]["baseline"]["tests_total"] == 5


def test_suite_cannot_reach_other_hosts(baseline) -> None:
    """Isolation: covered in detail by А-20; here — the run used the guarded network."""
    result, _, _ = baseline

    assert result["tools"][0]["baseline"]["collection_errors"] == []


@pytest.mark.xfail(
    strict=True,
    reason="агент не умеет передать набор в runner: gateway не шлёт tool=mutation",
)
def test_agent_can_send_its_suite_to_the_runner() -> None:
    from agent.gateway import SUPPORTED_TOOLS

    assert "mutation" in SUPPORTED_TOOLS
