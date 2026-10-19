"""Demo client: does what the AI agent does, against the docker-compose stand.

    python3 run_demo.py petstore            # all three tools
    python3 run_demo.py orders --tools schemathesis microcks

Builds a RunCreateRequest (contract by URL, the demo service as target, the sample
tests from sample_agent_tests/ for the mutation tool), POSTs it, polls the status,
prints the metrics and findings and saves the Markdown report next to this file.
Standard library only.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SUITES = HERE / "sample_agent_tests"
TEST_FILES = {"petstore": "test_pets.py", "orders": "test_orders.py"}
# mutation first: the fuzzers create and delete data, so they go last
TOOL_ORDER = ["mutation", "microcks", "schemathesis", "evomaster"]
CAUSES = {
    "service_defect": "дефект сервиса",
    "test_error": "ошибка теста",
    "infrastructure": "инфраструктура",
}


def request_body(service: str, tools: list[str]) -> dict[str, Any]:
    base = f"http://{service}:8080"
    configs: dict[str, dict[str, Any]] = {
        "mutation": {
            "tool": "mutation",
            "seed": 1,
            "tests": {
                "kind": "inline",
                "files": {
                    "conftest.py": (SUITES / "conftest.py").read_text(),
                    TEST_FILES[service]: (SUITES / service / TEST_FILES[service]).read_text(),
                },
            },
        },
        "microcks": {"tool": "microcks"},
        "schemathesis": {"tool": "schemathesis", "max_examples": 50, "seed": 1},
        # 308 "anonymous modifications" needs an auth model; the demo APIs have none
        "evomaster": {"tool": "evomaster", "max_time_s": 60, "seed": 1,
                      "disabled_oracle_codes": [308]},
    }  # fmt: skip
    return {
        "contract": {"kind": "url", "url": f"{base}/openapi.yaml"},
        "target": {"base_url": base},
        "tools": [configs[t] for t in TOOL_ORDER if t in tools],
        "labels": {"service": service, "source": "run_demo.py"},
    }


def call(api: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        api + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return bytes(resp.read())


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("service", choices=sorted(TEST_FILES))
    parser.add_argument("--tools", nargs="+", choices=TOOL_ORDER, default=TOOL_ORDER)
    parser.add_argument("--server", default="http://localhost:8000")
    args = parser.parse_args()
    api = args.server.rstrip("/") + "/api/v1"

    status, created = call(api, "POST", "/runs", request_body(args.service, args.tools))
    if status != 202:
        sys.exit(f"POST /runs -> {status}: {json.dumps(created, ensure_ascii=False)}")
    run_id = created["run_id"]
    print(f"Прогон {run_id} создан, инструменты: {', '.join(args.tools)}")

    while True:
        _, run = call(api, "GET", f"/runs/{run_id}")
        tools = "  ".join(
            f"{t['tool']}={t['state']}"
            + (f"({t['progress'] * 100:.0f}%)" if t["state"] == "running" and t["progress"] else "")
            for t in run["tools"]
        )
        print(f"\r  {run['state']:10} {tools}" + " " * 10, end="", flush=True)
        if run["state"] not in ("queued", "running"):
            break
        time.sleep(2)
    print()

    _, result = call(api, "GET", f"/runs/{run_id}/result")
    m = result["metrics"]
    print("\nМетрики")
    print(f"  Contract Coverage, операции:     {pct(m['contract_coverage'])}")
    print(f"  Contract Coverage, коды ответов: {pct(m['status_code_coverage'])}")
    if m["faults"]:
        print(f"  Находок black-box инструментов:  {m['faults']['findings_total']}")
    print(f"  Покрытие операций тестами агента: {pct(m['tests_contract_coverage'])}")
    print(f"  Покрытие кодов тестами агента:   {pct(m['tests_status_code_coverage'])}")
    size = m["test_suite_size"]
    print(f"  Test Suite Size:                 {size if size is not None else '—'}")
    print(f"  Runnability:                     {pct(m['runnability'])}")
    print(f"  Mutation Score:                  {pct(m['mutation_score'])}")

    for tool in result["tools"]:
        print(f"\n{tool['tool']}: {tool['state']}, {tool['duration_s'] or 0:.1f} с")
        if tool.get("error"):
            print(f"  ошибка: {tool['error']['code']}: {tool['error']['message']}")
        seen = set()
        for f in tool.get("findings", []):
            line = f"{f['operation']['method']} {f['operation']['path']}: {f['message'].splitlines()[0]}"
            if line not in seen:
                seen.add(line)
                print(f"  [{f['severity']}] {line[:110]}")
        if tool.get("summary") and tool["tool"] == "evomaster":
            print(f"  сгенерировано тестов: {tool['summary']['tests_generated']}")
        if tool.get("baseline"):
            print(f"  статус набора: {tool['baseline']['status']}")
            for t in tool["baseline"]["tests"]:
                if t["outcome"] != "passed":
                    msg = (t["message"] or "").splitlines()[0][:60] if t["message"] else ""
                    cause = CAUSES.get(t.get("cause") or "", "")
                    print(f"  тест {t['outcome']} [{cause}]: {t['node_id']}  {msg}")

    report = HERE / f"report-{args.service}-{run_id[:8]}.md"
    report.write_bytes(fetch(args.server.rstrip("/") + result["report"]["url"]))
    print(f"\nОтчёт: {report}")


if __name__ == "__main__":
    main()
