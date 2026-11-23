"""Compare a real ARQA-1 run with independent checks on clean Petstore instances."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import httpx
import yaml

EXPECTED = {
    "P1": "GET /pets?limit=101 → 400",
    "P2": 'POST /pets {"name": ""} → 422',
    "P3": "POST /pets → 201 with status in the response body",
    "P4": "DELETE /pets/999 → 404",
}
CONTRACT = Path(__file__).with_name("petstore") / "openapi.yaml"


def direct_checks(base_url: str) -> dict:
    with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
        control = client.get("/pets/1")
        control.raise_for_status()
        if control.json().get("status") != "available":
            raise ValueError("Use a fresh instance of Demo Petstore for direct checks")
        responses = {
            "P1": client.get("/pets", params={"limit": 101}),
            "P2": client.post("/pets", json={"name": ""}),
            "P3": client.post("/pets", json={"name": "tom", "tag": "cat"}),
            "P4": client.delete("/pets/999"),
        }
    observed = {
        key: {
            "expected": EXPECTED[key],
            "http": response.status_code,
            "body": response.json(),
        }
        for key, response in responses.items()
    }
    if (
        [item["http"] for item in observed.values()] != [500, 500, 201, 200]
        or "status" in observed["P3"]["body"]
        or observed["P4"]["body"] != {"deleted": False}
    ):
        raise ValueError(
            f"Petstore responses differ from the reference fixture: {observed}"
        )
    return observed


def defect(operation, path, query, body, status, missing_status=False):
    if operation == "GET /pets" and status == 500:
        try:
            if int(query.get("limit", 0)) > 100:
                return "P1"
        except (TypeError, ValueError):
            pass
    if (
        operation == "POST /pets"
        and status == 500
        and isinstance(body, dict)
        and body.get("name") == ""
    ):
        return "P2"
    if operation in {"POST /pets", "GET /pets", "GET /pets/{petId}"} and missing_status:
        return "P3"
    if operation == "DELETE /pets/{petId}" and path == "/pets/999" and status == 200:
        return "P4"
    return None


def tool_defects(report: dict) -> list[str]:
    detected = set()
    for run in report["tool_runs"]:
        for tool in run["tools"]:
            for finding in tool.get("findings", []):
                request = finding.get("request") or {}
                url = urlsplit(request.get("url", ""))
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                try:
                    body = json.loads(request.get("body") or "null")
                except ValueError:
                    body = None
                op = finding["operation"]
                matched = defect(
                    f"{op['method'].upper()} {op['path']}",
                    url.path,
                    query,
                    body,
                    (finding.get("response") or {}).get("status_code"),
                    finding["check"] == "response_schema_conformance"
                    and '"status" is a required property' in finding["message"],
                )
                if matched:
                    detected.add(matched)
    return sorted(detected)


def evaluate_tests(suite: Path, base_url: str, junit: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            str(suite),
            "-o",
            "junit_family=xunit1",
            f"--junitxml={junit}",
        ],
        env=os.environ | {"ARQA_BASE_URL": base_url},
        timeout=120,
        check=False,
    )
    if result.returncode not in {0, 1} or not junit.is_file():
        raise ValueError(
            f"pytest did not complete the suite (exit {result.returncode})"
        )
    tests = list(ElementTree.parse(junit).iter("testcase"))
    detected = set()
    observed = set()
    executed = 0
    for test in tests:
        properties = {
            prop.attrib["name"]: prop.attrib["value"]
            for prop in test.findall("properties/property")
        }
        responses = {
            key.removeprefix("response_"): json.loads(value)
            for key, value in properties.items()
            if key.startswith("response_")
        }
        if responses and test.find("error") is None and test.find("skipped") is None:
            executed += 1
        for index, response in responses.items():
            observed.add((response["operation"], str(response["actual"])))
            matched = defect(
                response["operation"],
                response["path"],
                response["query"],
                response["body"],
                response["actual"],
                "status" in json.loads(properties.get(f"missing_{index}", "[]")),
            )
            if matched and test.find("failure") is not None:
                detected.add(matched)
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    documented = {
        (f"{method.upper()} {path}", str(code))
        for path, item in contract["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict) and "responses" in operation
        for code in operation["responses"]
    }
    return {
        "test_suite_size": len(tests),
        "passed": sum(
            not any(
                test.find(state) is not None
                for state in ("failure", "error", "skipped")
            )
            for test in tests
        ),
        "failed": sum(test.find("failure") is not None for test in tests),
        "errors": sum(test.find("error") is not None for test in tests),
        "skipped": sum(test.find("skipped") is not None for test in tests),
        "runnability": [executed, len(tests)],
        "operation_coverage": [
            len({op for op, _ in observed}),
            len({op for op, _ in documented}),
        ],
        "status_code_coverage": [len(observed & documented), len(documented)],
        "defects": sorted(detected),
        "fault_detection_rate": [len(detected), len(EXPECTED)],
    }


def evaluate_mutation(tools_url: str, target: str, suite: Path) -> dict:
    payload = {
        "contract": {
            "kind": "inline",
            "format": "yaml",
            "content": CONTRACT.read_text(encoding="utf-8"),
        },
        "target": {"base_url": target},
        "tools": [
            {
                "tool": "mutation",
                "tests": {
                    "kind": "inline",
                    "files": {suite.name: suite.read_text(encoding="utf-8")},
                },
                "max_mutants": 30,
                "seed": 1,
                "per_mutant_timeout_s": 10,
            }
        ],
        "timeout_s": 120,
    }
    with httpx.Client(
        base_url=tools_url.rstrip("/") + "/", timeout=10, trust_env=False
    ) as client:
        response = client.post("runs", json=payload)
        response.raise_for_status()
        run = "runs/" + response.json()["run_id"]
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline:
            response = client.get(run)
            response.raise_for_status()
            if response.json()["state"] not in {"queued", "running"}:
                result = client.get(run + "/result")
                result.raise_for_status()
                return result.json()
            time.sleep(1)
    raise TimeoutError("Mutation run did not complete within its time limit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report", type=Path, help="report.json from a live ARQA-1 Petstore run"
    )
    parser.add_argument(
        "--reference-url",
        required=True,
        help="Clean Petstore for the independent checks",
    )
    parser.add_argument(
        "--pytest-url",
        required=True,
        help="Another clean Petstore for the generated tests",
    )
    parser.add_argument(
        "--mutation-url", help="Optional fourth clean Petstore for mutation checks"
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if (
        report["status"] != "success"
        or report["contract"].get("title") != "Demo Petstore"
    ):
        parser.error("Expected a successful Demo Petstore run")
    if (
        report["contract"]["sha256"]
        != hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    ):
        parser.error("The report must use this checkout's Demo Petstore contract")
    targets = [args.reference_url, args.pytest_url, report["target_base_url"]]
    if args.mutation_url:
        targets.append(args.mutation_url)
    if len({url.rstrip("/") for url in targets}) != len(targets):
        parser.error(
            "Use separate Petstore instances because tests change the service state"
        )

    reference = direct_checks(args.reference_url)
    detected = tool_defects(report)
    workdir = args.report.parent
    suite = workdir / "agent_artifacts" / "test_contract.py"
    if len(report["generated_tests"]) != 1 or not suite.is_file():
        parser.error("Expected agent_artifacts/test_contract.py from --generate-tests")
    result = {
        "reference": reference,
        "tools": {
            "defects": detected,
            "fault_detection_rate": [len(detected), len(reference)],
            "runs": report["metrics"]["tool_runs"],
        },
        "generated_pytest": evaluate_tests(
            suite, args.pytest_url, workdir / "generated-pytest.xml"
        ),
        "model": report["runtime"]["model"]["model"],
        "llm_calls": report["runtime"]["llm_calls"],
        "total_tokens": report["runtime"]["total_tokens"],
    }
    if args.mutation_url:
        mutation = evaluate_mutation(
            report["parameters"]["tools_url"], args.mutation_url, suite
        )
        result["mutation"] = {
            "run_id": mutation["run_id"],
            "state": mutation["state"],
            "metrics": mutation["metrics"],
            "summary": mutation["tools"][0].get("summary"),
        }
        (workdir / "petstore-mutation.json").write_text(
            json.dumps(mutation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    output = workdir / "petstore-evaluation.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
