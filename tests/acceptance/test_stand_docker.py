"""Control examples against the Docker stand (`docker compose`), not in-process services.

Runs inside the `tests` container of `tests/docker/compose.yml`: testing_tools, Microcks
and the demo services are real containers on the same network. Skipped elsewhere.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from conftest import (
    ORDERS_YAML,
    PETSTORE_YAML,
    cli_env,
    generate,
    inline_contract,
    inline_suite,
    scripted_llm,
    tools_run,
)

pytestmark = pytest.mark.stand

TOOLS = os.environ.get("ARQA_STAND_TOOLS_URL", "")
TARGETS = {
    "petstore": os.environ.get("ARQA_STAND_PETSTORE", ""),
    "orders": os.environ.get("ARQA_STAND_ORDERS", ""),
}
CONTRACTS = {"petstore": PETSTORE_YAML, "orders": ORDERS_YAML}

Finding = dict[str, Any]


def _status(f: Finding) -> int | None:
    return f["response"]["status_code"] if f.get("response") else None


def _op(f: Finding) -> str:
    return f"{f['operation']['method']} {f['operation']['path']}"


# Planted defects from demo/README.md and the findings that reveal them.
DEFECTS: dict[str, dict[str, Callable[[Finding], bool]]] = {
    "petstore": {
        "P1": lambda f: _op(f) == "GET /pets" and _status(f) == 500,
        "P2": lambda f: _op(f) == "POST /pets" and _status(f) == 500,
        "P3": lambda f: (
            re.search(r"[\"']status[\"']", f["message"]) is not None
            and "required" in f["message"]
        ),
        "P4": lambda f: _op(f) == "DELETE /pets/{petId}" and _status(f) == 200,
    },
    "orders": {
        "O1": lambda f: (
            _op(f) == "POST /orders"
            and _status(f) == 201
            and "quantity" in json.dumps(f)
        ),
        "O2": lambda f: _op(f) == "POST /orders" and "total" in f["message"],
        "O3": lambda f: _op(f) == "POST /orders/{orderId}/cancel" and _status(f) == 500,
        "O4": lambda f: "canceled" in json.dumps(f),
    },
}


def _found(service: str, findings: list[Finding]) -> set[str]:
    return {
        d
        for d, matches in DEFECTS[service].items()
        if any(matches(f) for f in findings)
    }


def test_stand_is_up() -> None:
    health = httpx.get(TOOLS.rsplit("/api/", 1)[0] + "/health", timeout=10).json()

    assert health["status"] == "ok", health
    assert health["tools"] == {"schemathesis": True, "microcks": True, "mutation": True}
    for url in TARGETS.values():
        assert httpx.get(url + "/openapi.yaml", timeout=10).status_code == 200


@pytest.fixture(scope="module")
def baselines() -> dict[str, dict[str, Any]]:
    """Schemathesis and Microcks on both demo services, through the containerized gateway."""
    return {
        service: tools_run(
            TOOLS,
            {
                "contract": {"kind": "url", "url": f"{target}/openapi.yaml"},
                "target": {"base_url": target},
                "tools": [
                    {"tool": "schemathesis", "max_examples": 50, "seed": 1},
                    {"tool": "microcks"},
                ],
                "labels": {"service": service, "suite": "stand"},
            },
            timeout=900,
        )
        for service, target in TARGETS.items()
    }


@pytest.mark.parametrize("service", ["petstore", "orders"])
def test_both_baselines_run(service: str, baselines: dict[str, dict[str, Any]]) -> None:
    """А-14: Schemathesis and a second baseline (Microcks) on the same service."""
    result = baselines[service]

    assert result["state"] == "completed", result["error"]
    schemathesis, microcks = result["tools"]
    assert schemathesis["state"] in {"passed", "failed"}, schemathesis["error"]
    assert microcks["state"] in {"passed", "failed"}, microcks["error"]
    assert result["metrics"]["contract_coverage"] == 1.0
    assert set(result["metrics"]["duration_s"]) == {"schemathesis", "microcks"}


def test_fault_detection_table(baselines: dict[str, dict[str, Any]]) -> None:
    """А-14: FDR of each baseline over the planted defects; printed for the report."""
    rows = []
    for service, result in baselines.items():
        for tool in result["tools"]:
            found = _found(service, tool.get("findings", []))
            rows.append((service, tool["tool"], sorted(found), len(DEFECTS[service])))
    print("\nFDR on the Docker stand:")
    for service, tool, found, total in rows:
        print(f"  {service:9} {tool:13} {len(found)}/{total} {found}")
    schemathesis_petstore = next(
        r for r in rows if r[:2] == ("petstore", "schemathesis")
    )
    assert schemathesis_petstore[2] == ["P1", "P2", "P3", "P4"]


def test_contract_from_a_demo_container(workdir: Path) -> None:
    """А-1: the contract is downloaded by URL from a running service."""
    url = TARGETS["petstore"] + "/openapi.yaml"
    with scripted_llm() as llm:
        run = generate(url, TARGETS["petstore"], env=cli_env(llm), cwd=workdir)

    assert run.code == 0, run.stderr
    assert run.report["contract"]["title"] == "Demo Petstore"
    assert run.report["contract"]["input_format"] == "yaml"


def test_unknown_target_is_infrastructure_failure() -> None:
    """А-8, А-16: a target that does not resolve fails as target_unreachable."""
    result = tools_run(
        TOOLS,
        {
            "contract": inline_contract(PETSTORE_YAML),
            "target": {"base_url": "http://no-such-service.invalid:8080"},
            "tools": [{"tool": "schemathesis", "max_examples": 5}],
        },
        timeout=120,
    )

    assert result["state"] == "failed"
    assert result["error"]["code"] == "target_unreachable"


def test_agent_tests_cannot_reach_other_containers() -> None:
    """А-20: inside the gateway container, a test may only reach the service under test."""
    suite = {
        "tests/conftest.py": (
            "import os, httpx, pytest\n\n@pytest.fixture\ndef api():\n"
            "    with httpx.Client(base_url=os.environ['ARQA_BASE_URL']) as c:\n        yield c\n"
        ),
        "tests/test_suite.py": (
            "import httpx\n\n"
            "def test_service(api):\n    assert api.get('/pets').status_code == 200\n\n"
            "def test_other_container():\n    httpx.get('http://microcks:8080/api/health', timeout=3)\n"
        ),
    }
    result = tools_run(
        TOOLS,
        {
            "contract": inline_contract(PETSTORE_YAML),
            "target": {"base_url": TARGETS["petstore"]},
            "tools": [
                {"tool": "mutation", "tests": inline_suite(suite), "max_mutants": 1}
            ],
        },
    )

    tests = {
        t["node_id"].split("::")[-1]: t for t in result["tools"][0]["baseline"]["tests"]
    }
    assert tests["test_service"]["outcome"] == "passed"
    assert tests["test_other_container"]["outcome"] == "failed"
    assert "is blocked" in tests["test_other_container"]["message"]


def _real_run(workdir: Path, *tools: str) -> Any:
    target = TARGETS["petstore"]
    return generate(
        f"{target}/openapi.yaml",
        target,
        env=cli_env(),
        cwd=workdir,
        extra=(
            "--tools-url", TOOLS, "--tools", *tools,
            "--tools-timeout", "600", "--agent-timeout", "1500", "--max-steps", "25",
        ),
        timeout=1600,
    )  # fmt: skip


def _started(run: Any) -> list[str]:
    return [
        json.loads(line)["tool"]
        for line in (run.run_dir / "events.jsonl").read_text("utf-8").splitlines()
        if '"gateway.started"' in line
    ]


def _summary(run: Any) -> str:
    runtime = run.report["runtime"] or {}
    return (
        f"status={run.report['status']} execution={(run.report['execution'] or {}).get('state')} "
        f"tools={_started(run)} llm_calls={runtime.get('llm_calls')} tokens={runtime.get('total_tokens')} "
        f"error={run.report['error']}"
    )


@pytest.mark.llm
def test_real_model_full_scenario(workdir: Path) -> None:
    """Contract → agent → real model → containerized tools → report, on the demo Petstore."""
    run = _real_run(workdir, "schemathesis", "microcks")
    print("\nreal model, full scenario:", _summary(run))
    print(str(run.report["agent"] and run.report["agent"]["output"])[:3000])

    assert run.code == 0, run.report["error"]
    assert _started(run), "the model called no tool"
    assert run.report["execution"]["state"] == "failed", (
        "the demo Petstore has planted defects"
    )


@pytest.mark.llm
def test_real_model_uses_the_chosen_tool(workdir: Path) -> None:
    """А-25: only Microcks is allowed, so the model calls Microcks."""
    run = _real_run(workdir, "microcks")
    print("\nreal model, microcks only:", _summary(run))

    assert run.code == 0, run.report["error"]
    assert set(_started(run)) == {"microcks"}
