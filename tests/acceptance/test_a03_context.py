"""А-3 (Ф-3): подготовка контекста без исходного кода.

Шаги: генерация без пути к исходному коду по контракту со ссылками $ref и связанными
операциями; просмотр summary и журнала.
Ожидается: генерация выполнена; ссылки разрешены; summary содержит элементы
п. 4.1.2.4 ТЗ и связи операций, контекст — сводку Schemathesis; полный файл контракта
в LLM не передан.

Генерация тестов проверяется в А-4; здесь — подготовка контекста.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    PETSTORE_YAML,
    cli_env,
    demo_service,
    generate,
    scripted_llm,
    tool_call,
    tools_service,
)

MARKER = "NOT-FOR-THE-MODEL-7f3a"

CONTRACT: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Linked Pets", "version": "2.1.0", "x-owner-note": MARKER},
    "paths": {
        "/pets": {
            "post": {
                "operationId": "createPet",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/NewPet"}
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "created",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                        "links": {
                            "GetCreatedPet": {
                                "operationId": "getPet",
                                "parameters": {"petId": "$response.body#/id"},
                            }
                        },
                    },
                    "422": {"$ref": "#/components/responses/Invalid"},
                },
            }
        },
        "/pets/{petId}": {
            "parameters": [{"$ref": "#/components/parameters/PetId"}],
            "get": {
                "operationId": "getPet",
                "responses": {
                    "200": {
                        "description": "pet",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                    },
                    "404": {"description": "not found"},
                },
            },
        },
    },
    "components": {
        "parameters": {
            "PetId": {
                "name": "petId",
                "in": "path",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
            }
        },
        "responses": {"Invalid": {"description": "validation failed"}},
        "schemas": {
            "NewPet": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 50}
                },
            },
            "Pet": {
                "allOf": [
                    {"$ref": "#/components/schemas/NewPet"},
                    {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "integer"}},
                    },
                ]
            },
            "Unused": {"type": "object", "description": MARKER},
        },
    },
}


def _run(workdir: Path, contract: dict[str, Any]):
    path = workdir / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with scripted_llm(final="Контекст получен.") as llm:
        run = generate(path, "http://127.0.0.1:9", env=cli_env(llm), cwd=workdir)
    assert run.code == 0, run.stderr
    summary = json.loads((run.run_dir / "contract-summary.json").read_text("utf-8"))
    return run, summary, llm


def _operation(summary: dict[str, Any], operation_id: str) -> dict[str, Any]:
    return next(
        op for op in summary["operations"] if op.get("operationId") == operation_id
    )


def test_refs_are_resolved(workdir: Path) -> None:
    _, summary, _ = _run(workdir, CONTRACT)

    get_pet = _operation(summary, "getPet")
    assert get_pet["parameters"][0]["name"] == "petId"
    assert get_pet["parameters"][0]["schema"]["minimum"] == 1
    create = _operation(summary, "createPet")
    assert create["responses"]["422"]["description"] == "validation failed"
    pet = summary["schemas"]["#/components/schemas/Pet"]
    assert "$ref" not in json.dumps(summary)
    assert pet["allOf"][0] == {"schema_ref": "#/components/schemas/NewPet"}
    assert (
        summary["schemas"]["#/components/schemas/NewPet"]["properties"]["name"][
            "maxLength"
        ]
        == 50
    )


def test_explicit_links_become_dependencies(workdir: Path) -> None:
    _, summary, _ = _run(workdir, CONTRACT)

    [dependency] = summary["dependencies"]
    assert dependency["source"] == {
        "method": "POST",
        "path": "/pets",
        "status_code": "201",
    }
    assert dependency["operationId"] == "getPet"
    assert dependency["parameters"] == {"petId": "$response.body#/id"}


@pytest.mark.xfail(
    strict=True,
    reason="связи берутся только из явных links; эвристика parser (produces_ids/consumes_ids) "
    "в summary для LLM не попадает",
)
def test_implicit_id_relation_becomes_dependency(workdir: Path) -> None:
    contract = json.loads(json.dumps(CONTRACT))
    del contract["paths"]["/pets"]["post"]["responses"]["201"]["links"]

    _, summary, _ = _run(workdir, contract)

    assert summary["dependencies"], "createPet returns id, getPet takes petId"


def test_full_contract_never_reaches_the_model(workdir: Path) -> None:
    _, summary, llm = _run(workdir, CONTRACT)

    sent = llm.sent_text()
    assert "createPet" in sent and "getPet" in sent
    assert MARKER not in sent, "vendor extensions and unused schemas must not be sent"
    assert MARKER not in json.dumps(summary)


def test_journal_records_context_preparation(workdir: Path) -> None:
    run, _, _ = _run(workdir, CONTRACT)

    events = [
        json.loads(line)
        for line in (run.run_dir / "events.jsonl").read_text("utf-8").splitlines()
    ]
    names = [e["event"] for e in events]
    assert names[0] == "run.started" and names[-1] == "run.finished"
    loaded = next(e for e in events if e["event"] == "contract.loaded")
    assert loaded["title"] == "Linked Pets" and loaded["operations_total"] == 2
    prepared = next(e for e in events if e["event"] == "context.prepared")
    assert prepared["operations"] == 2 and prepared["summary_chars"] > 0


def test_context_gets_schemathesis_summary(workdir: Path, tmp_path: Path) -> None:
    with demo_service("petstore") as target, tools_service(tmp_path) as tools:
        with scripted_llm(
            tool_call("run_api_checks", tool_name="schemathesis", max_examples=5)
        ) as llm:
            run = generate(
                PETSTORE_YAML,
                target,
                env=cli_env(llm),
                cwd=workdir,
                extra=("--tools-url", tools, "--tools-timeout", "300"),
                timeout=400,
            )

    assert run.code == 0, run.stderr
    [feedback] = llm.tool_results()
    compact = json.loads(feedback)
    [schemathesis] = compact["tools"]
    assert schemathesis["tool"] == "schemathesis"
    assert schemathesis["summary"]["operations_tested"] == 4
    assert schemathesis["coverage"]["operations_total"] == 4
    assert schemathesis["findings"], "demo petstore has planted defects"
    assert len(schemathesis["findings"]) <= 10
    assert len(feedback) <= 24_000
