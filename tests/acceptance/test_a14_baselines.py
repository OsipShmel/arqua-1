"""А-14 (К-5): сравнение с baseline-инструментами.

Шаги: запуск Schemathesis и второго baseline-инструмента на тех же микросервисах.
Ожидается: сравнительная таблица; CC и FDR системы не ниже Schemathesis; указаны число
обращений к LLM, токены, время.

Второй baseline — Microcks (маркер `docker`). Эталон дефектов — `demo/README.md`.
"""

import os
from pathlib import Path

import pytest
from conftest import (
    ORDERS_YAML,
    PETSTORE_YAML,
    demo_app,
    inline_contract,
    port_of,
    serve,
    tools_run,
    tools_service,
)

CONTRACTS = {"petstore": PETSTORE_YAML, "orders": ORDERS_YAML}
# Planted defects (demo/README.md) and the findings that reveal them.
DEFECTS = {
    "petstore": {
        "P1": lambda f: (
            f["operation"]["path"] == "/pets"
            and f["operation"]["method"] == "GET"
            and f["response"]
            and f["response"]["status_code"] == 500
        ),
        "P2": lambda f: (
            f["operation"]["method"] == "POST"
            and f["response"]
            and f["response"]["status_code"] == 500
        ),
        "P3": lambda f: (
            f["check"] == "response_schema_conformance" and "status" in f["message"]
        ),
        "P4": lambda f: (
            f["operation"]["method"] == "DELETE"
            and f["response"]
            and f["response"]["status_code"] == 200
        ),
    },
}


def _schemathesis(service: str, tmp_path: Path) -> dict:
    with serve(demo_app(service)) as target, tools_service(tmp_path) as tools:
        return tools_run(
            tools,
            {
                "contract": inline_contract(CONTRACTS[service]),
                "target": {"base_url": target},
                "tools": [{"tool": "schemathesis", "max_examples": 50, "seed": 1}],
            },
            timeout=600,
        )


def test_schemathesis_baseline_finds_planted_defects(tmp_path: Path) -> None:
    result = _schemathesis("petstore", tmp_path)

    findings = result["tools"][0]["findings"]
    found = {
        d
        for d, matches in DEFECTS["petstore"].items()
        if any(matches(f) for f in findings)
    }
    assert found == {"P1", "P2", "P3", "P4"}
    assert result["metrics"]["contract_coverage"] == 1.0


@pytest.mark.docker
@pytest.mark.parametrize("service", ["petstore", "orders"])
def test_microcks_is_the_second_baseline(service: str, tmp_path: Path) -> None:
    # Microcks runs in Docker and calls the target itself, so the target listens on 0.0.0.0.
    host = os.environ.get("ARQA_TARGET_HOST", "host.docker.internal")
    with serve(demo_app(service), host="0.0.0.0") as local:
        target = f"http://{host}:{port_of(local)}"
        with tools_service(
            tmp_path, microcks_url=os.environ["ARQA_MICROCKS_URL"]
        ) as tools:
            result = tools_run(
                tools,
                {
                    "contract": inline_contract(CONTRACTS[service]),
                    "target": {"base_url": target},
                    "tools": [
                        {"tool": "schemathesis", "max_examples": 50, "seed": 1},
                        {"tool": "microcks"},
                    ],
                },
                timeout=600,
            )

    schemathesis, microcks = result["tools"]
    assert microcks["state"] in {"passed", "failed"}, microcks["error"]
    assert (
        microcks["coverage"]["operations_total"]
        == schemathesis["coverage"]["operations_total"]
    )
    assert result["metrics"]["duration_s"].keys() == {"schemathesis", "microcks"}


@pytest.mark.xfail(
    strict=True,
    reason="сравнительной таблицы «система vs Schemathesis vs Microcks» нет; у системы нет своих тестов",
)
def test_system_is_not_worse_than_schemathesis(tmp_path: Path) -> None:
    result = _schemathesis("petstore", tmp_path)

    assert "comparison" in result["metrics"]
