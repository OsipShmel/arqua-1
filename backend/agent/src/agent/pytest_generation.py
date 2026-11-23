"""Turn validated contract cases into a pytest file using a fixed template."""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent.results import ResultFile

CATEGORY = Literal["normal", "boundary", "error", "scenario"]
PARAMETER = re.compile(r"{([A-Za-z0-9_-]+)}")
SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    status: int = Field(ge=100, le=599)
    path_params: dict[str, str | int] = Field(default_factory=dict)
    query: dict[str, str | int | bool] = Field(default_factory=dict)
    body: JsonValue = None
    previous_id_param: str | None = None


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    category: CATEGORY
    steps: list[Step] = Field(min_length=1, max_length=5)


def safe_segment(value: str | int) -> str:
    segment = str(value)
    if not SEGMENT.fullmatch(segment) or segment in {".", ".."}:
        raise ValueError("Параметр пути должен занимать один сегмент")
    return segment


def resolve(contract: dict, value: dict) -> dict:
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        value = contract
        for key in ref[2:].split("/"):
            value = value.get(key.replace("~1", "/").replace("~0", "~"), {})
            if not isinstance(value, dict):
                return {}
    return value if isinstance(value, dict) else {}


def required_fields(contract: dict, response: dict) -> tuple[str | None, list[str]]:
    response = resolve(contract, response)
    schema = resolve(
        contract,
        response.get("content", {}).get("application/json", {}).get("schema", {}),
    )
    if schema.get("type") == "array":
        items = resolve(contract, schema.get("items", {}))
        return "array", items.get("required", [])
    if schema.get("type") == "object":
        return "object", schema.get("required", [])
    return None, []


def validated_cases(cases: list[Case], contract: dict) -> list[dict]:
    if not 1 <= len(cases) <= 40:
        raise ValueError("Укажи от 1 до 40 тестов")
    names = set()
    planned = []
    for case in cases:
        is_scenario = case.category == "scenario"
        if is_scenario != (len(case.steps) > 1) or case.name in names:
            raise ValueError(
                f"{case.name}: имя должно быть уникальным, сценарий — многошаговым"
            )
        names.add(case.name)
        steps = []
        for index, step in enumerate(case.steps):
            path = resolve(contract, contract["paths"].get(step.path, {}))
            if not step.path.startswith("/") or "//" in step.path:
                raise ValueError(f"{case.name}: недопустимый путь")
            for piece in PARAMETER.sub("parameter", step.path).split("/")[1:]:
                if piece:
                    safe_segment(piece)
            operation = path.get(step.method.lower(), {})
            response = operation.get("responses", {}).get(str(step.status))
            if response is None:
                raise ValueError(
                    f"{case.name}: операция или код отсутствует в контракте"
                )
            params = set(step.path_params)
            if step.previous_id_param is not None:
                if not index or step.previous_id_param in params:
                    raise ValueError(f"{case.name}: нужен id из предыдущего шага")
                params.add(step.previous_id_param)
            expected_params = set(PARAMETER.findall(step.path))
            if params != expected_params:
                raise ValueError(
                    f"{case.name} шаг {index + 1} {step.path}: "
                    f"укажи ровно {sorted(expected_params)} в path_params или "
                    f"previous_id_param; получено {sorted(params)}"
                )
            for value in step.path_params.values():
                safe_segment(value)
            parameters = [
                resolve(contract, raw)
                for raw in path.get("parameters", []) + operation.get("parameters", [])
            ]
            declared_query = {
                item.get("name") for item in parameters if item.get("in") == "query"
            }
            if step.query.keys() - declared_query:
                raise ValueError(f"{case.name}: неизвестный query-параметр")
            if step.body is not None and "requestBody" not in operation:
                raise ValueError(f"{case.name}: тело запроса отсутствует в контракте")
            shape, required = required_fields(contract, response)
            steps.append(step.model_dump() | {"shape": shape, "required": required})
        planned.append({"name": case.name, "category": case.category, "steps": steps})
    return planned


TEMPLATE = r"""
import json
import os
import re
from datetime import UTC, datetime

import httpx
import pytest

CASES = json.loads(__CASES__)


@pytest.fixture
def api_client():
    with httpx.Client(
        base_url=os.environ["ARQA_BASE_URL"],
        timeout=10,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield client


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_api_contract(case, api_client, record_property):
    record_property("category", case["category"])
    previous = None
    for index, step in enumerate(case["steps"]):
        params = dict(step["path_params"])
        if step["previous_id_param"] is not None:
            resource_id = previous.get("id") if isinstance(previous, dict) else None
            if not isinstance(resource_id, (int, str)) or isinstance(resource_id, bool):
                pytest.fail("Предыдущий шаг не вернул id")
            segment = str(resource_id)
            if not re.fullmatch(r"[A-Za-z0-9._~-]+", segment) or segment in {".", ".."}:
                pytest.fail("Предыдущий шаг вернул недопустимый id")
            params[step["previous_id_param"]] = segment

        path = step["path"].format_map(params)
        response = api_client.request(
            step["method"], path, params=step["query"], json=step["body"]
        )
        operation = f'{step["method"]} {step["path"]}'
        record_property(
            f"response_{index}",
            json.dumps({"operation": operation, "actual": response.status_code,
                        "expected": step["status"], "path": path,
                        "query": step["query"], "body": step["body"]}),
        )
        assert response.status_code == step["status"], (
            f"{operation}: expected {step['status']}, got {response.status_code}"
        )
        previous = None
        if step["shape"] is not None:
            previous = response.json()
            if step["shape"] == "object":
                assert isinstance(previous, dict), operation
                items = [previous]
            else:
                assert isinstance(previous, list), operation
                items = previous
            if step["required"]:
                for item in items:
                    assert isinstance(item, dict), operation
                    missing = set(step["required"]).difference(item)
                    if missing:
                        record_property(f"missing_{index}", json.dumps(sorted(missing)))
                        pytest.fail(f"{operation}: missing required fields {sorted(missing)}")
"""


class PytestGeneration:
    def __init__(self, contract: dict, metadata: dict, journal):
        self.contract = contract
        self.metadata = metadata
        self.journal = journal
        self.files: list[ResultFile] = []
        self.count = 0

    def as_tool(self):
        @tool
        def save_pytest_cases(cases: list[Case]) -> str:
            """Сохранить pytest-кейсы по контракту. Передай полный набор кейсов.
            method и path бери из OpenAPI, status — ожидаемый документированный код.
            Имена параметров регистрозависимы. Для /items/{itemId} укажи
            path_params={"itemId": 1} или, для id прошлого ответа, previous_id_param="itemId"
            и оставь path_params пустым. Категории: normal, boundary, error, scenario.
            """
            try:
                planned = validated_cases(cases, self.contract)
                data = json.dumps(planned, ensure_ascii=True, allow_nan=False)
                if len(data) > 200_000:
                    raise ValueError("Превышен лимит размера набора")
            except ValueError as exc:
                self.journal.emit("pytest.rejected", message=str(exc))
                return f"Набор отклонён. Исправь данные: {exc}"
            provenance = json.dumps(
                self.metadata | {"generated_at": datetime.now(UTC).isoformat()},
                ensure_ascii=True,
            )
            self.files = [
                ResultFile(
                    path="test_contract.py",
                    kind="pytest",
                    content=f"# ARQA-1: {provenance}\n"
                    + TEMPLATE.lstrip().replace("__CASES__", repr(data), 1),
                )
            ]
            self.count = len(cases)
            self.journal.emit("pytest.generated", test_suite_size=self.count)
            return f"Подготовлено pytest-кейсов: {self.count}. Файл ещё не запускался."

        return save_pytest_cases
