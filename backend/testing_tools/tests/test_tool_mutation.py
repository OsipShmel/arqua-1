"""MutationRunner against the buggy petstore: real proxy, real pytest subprocesses."""

import subprocess
from pathlib import Path

import pytest

from testing_tools.api.schemas import (
    ErrorCode,
    MutantState,
    MutationConfig,
    MutationResult,
    ToolState,
)
from testing_tools.api.schemas import TestOutcome as Outcome
from testing_tools.app.contract import LoadedContract
from testing_tools.app.tools.base import ToolError
from testing_tools.app.tools.mutation import MutationRunner
from tool_helpers import make_ctx

pytestmark = pytest.mark.anyio

CONFTEST = """
import os, httpx, pytest

@pytest.fixture
def client():
    with httpx.Client(base_url=os.environ["ARQA_BASE_URL"]) as c:
        yield c
"""
TESTS = """
def test_list(client):
    r = client.get("/pets")
    assert r.status_code == 200
    for pet in r.json():
        assert isinstance(pet["id"], int)

def test_missing(client):
    assert client.get("/pets/999").status_code == 404

def test_wrong(client):
    assert client.get("/pets").status_code == 418
"""


def config(files: dict[str, str] | None = None, **fields: object) -> MutationConfig:
    suite = {"kind": "inline", "files": files or {"conftest.py": CONFTEST, "test_pets.py": TESTS}}
    return MutationConfig.model_validate({"tests": suite, "seed": 1, **fields})


async def test_baseline_and_mutants(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, collected = make_ctx(tmp_path, contract, petstore_url)
    cfg = config(operations={"include": {"operation_ids": ["listPets", "getPet"]}})

    result = await MutationRunner().run(cfg, ctx)

    assert isinstance(result, MutationResult)
    assert result.state == ToolState.FAILED  # test_wrong fails on baseline
    baseline = result.baseline
    assert baseline is not None
    assert (baseline.tests_total, baseline.passed, baseline.failed) == (3, 2, 1)
    assert baseline.runnability == pytest.approx(2 / 3)
    outcomes = {t.node_id: t.outcome for t in baseline.tests}
    assert outcomes["test_pets.py::test_wrong"] == Outcome.FAILED
    assert "418" in (next(t for t in baseline.tests if t.node_id.endswith("wrong")).message or "")

    mutants = {m.description: m for m in result.mutants}
    killed = mutants["integer -> str in /*/id"]
    assert killed.state == MutantState.KILLED
    assert killed.killed_by == ["test_pets.py::test_list"]
    assert mutants["string -> int in /*/name"].state == MutantState.SURVIVED
    assert mutants["status 404 -> 200"].state == MutantState.KILLED
    # nobody requests GET /pets/{id} with a 200 answer -> never reached
    assert all(
        m.state == MutantState.NO_COVERAGE
        for m in result.mutants
        if m.operation.operation_id == "getPet" and m.status_code == "200"
    )
    assert all(m.operation.operation_id != "createPet" for m in result.mutants)

    summary = result.summary
    assert summary is not None
    assert summary.mutants_total == len(result.mutants)
    scored = summary.mutants_total - summary.no_coverage - summary.errors
    assert summary.mutation_score == pytest.approx(round(summary.killed / scored, 4))
    assert set(summary.by_operator) == set(cfg.operators)
    assert {"mutation-baseline-junit.xml", "mutation-pytest.log"} <= set(collected.artifacts)
    assert collected.progress[-1] == pytest.approx(0.95)


async def test_all_green_suite_without_survivors_passes(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)
    tests = TESTS.split("def test_wrong")[0]
    cfg = config(
        {"conftest.py": CONFTEST, "test_pets.py": tests},
        operators=["replace_status_code"],
        operations={"include": {"operation_ids": ["listPets"]}},
    )

    result = await MutationRunner().run(cfg, ctx)

    assert isinstance(result, MutationResult)
    assert result.state == ToolState.PASSED
    assert result.summary is not None
    assert (result.summary.mutants_total, result.summary.killed) == (1, 1)
    assert result.summary.mutation_score == 1.0


async def test_collection_errors_count_against_runnability(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)
    files = {"conftest.py": CONFTEST, "test_ok.py": TESTS, "test_broken.py": "import nope\n"}

    result = await MutationRunner().run(
        config(files, operators=["replace_status_code"], max_mutants=1), ctx
    )

    assert isinstance(result, MutationResult)
    baseline = result.baseline
    assert baseline is not None
    assert baseline.collection_errors == ["test_broken.py"]
    assert (baseline.tests_total, baseline.errors) == (4, 1)


async def test_git_suite(tmp_path: Path, contract: LoadedContract, petstore_url: str) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "conftest.py").write_text(CONFTEST)
    (repo / "tests" / "test_pets.py").write_text(TESTS)
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "tests"], check=True)
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)
    cfg = MutationConfig.model_validate(
        {
            "tests": {"kind": "git", "repo_url": str(repo), "subdir": "tests"},
            "operators": ["replace_status_code"],
            "operations": {"include": {"operation_ids": ["listPets"]}},
        }
    )

    result = await MutationRunner().run(cfg, ctx)

    assert isinstance(result, MutationResult)
    assert result.baseline is not None and result.baseline.tests_total == 3


async def test_bad_git_repo_is_a_suite_error(tmp_path: Path, contract: LoadedContract) -> None:
    ctx, _ = make_ctx(tmp_path, contract, "http://127.0.0.1:9")
    cfg = MutationConfig.model_validate(
        {"tests": {"kind": "git", "repo_url": str(tmp_path / "nope")}}
    )

    with pytest.raises(ToolError) as exc:
        await MutationRunner().run(cfg, ctx)

    assert exc.value.code == ErrorCode.TEST_SUITE_INVALID


async def test_option_like_requirements_are_rejected(
    tmp_path: Path, contract: LoadedContract
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, "http://127.0.0.1:9")

    with pytest.raises(ToolError) as exc:
        await MutationRunner().run(config(requirements=["--index-url=http://evil"]), ctx)

    assert exc.value.code == ErrorCode.TEST_SUITE_INVALID


async def test_bad_pytest_args_are_a_suite_error(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)

    with pytest.raises(ToolError) as exc:
        await MutationRunner().run(config(pytest_args=["--no-such-flag"]), ctx)

    assert exc.value.code == ErrorCode.TEST_SUITE_INVALID
    assert "output_tail" in exc.value.details


async def test_baseline_timeout(
    tmp_path: Path, contract: LoadedContract, petstore_url: str
) -> None:
    ctx, _ = make_ctx(tmp_path, contract, petstore_url)
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"

    with pytest.raises(ToolError) as exc:
        await MutationRunner().run(config({"test_slow.py": slow}, per_mutant_timeout_s=1), ctx)

    assert exc.value.code == ErrorCode.TIMEOUT
