"""Agent-owned client for develop's testing_tools API. CLI never calls this API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from langchain_core.tools import tool

from agent.contracts import http_url
from agent.events import Journal, atomic_json
from agent.results import safe_relative_path

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class ToolsGateway:
    def __init__(self, options: dict, journal: Journal):
        self.url = http_url(options["url"]).rstrip("/") + "/"
        if urlsplit(self.url).query or urlsplit(self.url).fragment:
            raise ValueError("URL tools API не должен содержать query или fragment")
        self.contract = options["contract"]
        self.target = options["target"]
        self.allowed = options.get("tools", ["schemathesis"])
        self.timeout = options.get("timeout", 120)
        self.directory = Path(options["directory"]).resolve()
        self.journal = journal
        self.runs: list[dict] = []
        self.artifacts: list[dict] = []

    def run(self, name: str, max_examples: int = 50) -> str:
        if name not in self.allowed or name not in {"schemathesis", "microcks"}:
            raise ValueError("Этот инструмент не разрешён параметрами запуска")
        if not 1 <= max_examples <= 10_000:
            raise ValueError("max_examples должен быть от 1 до 10000")
        config = {"tool": name}
        if name == "schemathesis":
            config["max_examples"] = max_examples
        payload = {
            "contract": {
                "kind": "inline",
                "format": "json",
                "content": json.dumps(self.contract, ensure_ascii=False),
            },
            "target": {"base_url": self.target},
            "tools": [config],
            "timeout_s": self.timeout,
            "labels": {"arqa_run_id": self.journal.run_id},
        }
        deadline = time.monotonic() + self.timeout
        run_id = None
        with httpx.Client(
            base_url=self.url, timeout=10.0, follow_redirects=False
        ) as client:
            try:
                created = self._request(
                    client, "POST", "runs", deadline, json=payload
                ).json()
                run_id = str(UUID(created["run_id"]))
                folder = (self.directory / "tool_artifacts" / run_id).resolve()
                if not folder.is_relative_to(self.directory):
                    raise ValueError("Каталог tools выходит за каталог прогона")
                folder.mkdir(parents=True, exist_ok=False)
                atomic_json(folder / "request.json", payload)
                self.journal.emit("gateway.started", tool=name, tools_run_id=run_id)
                while True:
                    status = self._request(
                        client, "GET", f"runs/{run_id}", deadline
                    ).json()
                    if status["state"] in TERMINAL:
                        break
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                result = self._request(
                    client, "GET", f"runs/{run_id}/result", deadline
                ).json()
                if result.get("run_id") != run_id:
                    raise ValueError("Tools-сервис вернул результат другого прогона")
                if result.get("state") not in TERMINAL:
                    raise ValueError("Tools-сервис вернул незавершённый результат")
                self.runs.append(result)
                atomic_json(folder / "result.json", result)
                references = [
                    item
                    for t in result.get("tools", [])
                    for item in t.get("artifacts", [])
                ]
                if result.get("report"):
                    references.append(result["report"])
                saved, size = set(), 0
                for item in references:
                    artifact_name = item["name"]
                    relative = safe_relative_path(artifact_name)
                    if len(relative.parts) != 1 or artifact_name.casefold() in {
                        "result.json",
                        "request.json",
                    }:
                        raise ValueError("Недопустимое имя артефакта tools")
                    if artifact_name.casefold() in saved:
                        continue
                    saved.add(artifact_name.casefold())
                    # Construct the endpoint ourselves; never follow a URL supplied in a result.
                    with client.stream(
                        "GET",
                        f"runs/{run_id}/artifacts/{quote(artifact_name, safe='')}",
                        timeout=self._remaining(deadline),
                    ) as response:
                        response.raise_for_status()
                        chunks = []
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 10_000_000:
                                raise ValueError(
                                    "Артефакты tools превышают лимит 10 MB"
                                )
                            chunks.append(chunk)
                    path = folder / artifact_name
                    path.write_bytes(b"".join(chunks))
                    self.artifacts.append(
                        {
                            "path": str(path),
                            "media_type": item.get("media_type"),
                            "tools_run_id": run_id,
                        }
                    )
                compact = {
                    "run_id": run_id,
                    "state": result["state"],
                    "metrics": result.get("metrics"),
                    "tools": [],
                }
                for result_tool in result.get("tools", []):
                    compact["tools"].append(
                        {
                            k: result_tool[k]
                            for k in ("tool", "state", "summary", "coverage", "error")
                            if k in result_tool
                        }
                        | {"findings": result_tool.get("findings", [])[:10]}
                    )
                compact["error"] = result.get("error")
                self.journal.emit(
                    "gateway.finished",
                    tool=name,
                    tools_run_id=run_id,
                    state=result["state"],
                )
                # Bound feedback independently of the raw reports saved on disk.
                if len(json.dumps(compact, ensure_ascii=False)) > 24_000:
                    compact["tools"] = [
                        {
                            k: t[k]
                            for k in ("tool", "state", "summary", "error")
                            if k in t
                        }
                        for t in compact["tools"]
                    ]
                    compact["details_omitted"] = True
                if len(json.dumps(compact, ensure_ascii=False)) > 24_000:
                    compact = {
                        "run_id": run_id,
                        "state": result["state"],
                        "tools": [
                            {"tool": t.get("tool"), "state": t.get("state")}
                            for t in result.get("tools", [])
                        ],
                        "details_omitted": True,
                        "note": "Подробный результат сохранён в артефактах прогона",
                    }
                return json.dumps(compact, ensure_ascii=False)
            except BaseException:
                if run_id is not None:
                    try:
                        client.post(f"runs/{run_id}/cancel", timeout=2.0)
                    except httpx.HTTPError:
                        pass
                raise

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Истёк срок ожидания tools-сервиса")
        return min(10.0, remaining)

    def _request(self, client, method, path, deadline, **kwargs):
        response = client.request(
            method, path, timeout=self._remaining(deadline), **kwargs
        )
        response.raise_for_status()
        return response

    def as_tools(self):
        @tool
        def run_api_checks(
            tool_name: str = "schemathesis", max_examples: int = 50
        ) -> str:
            """Запустить разрешённый Schemathesis или Microcks через tools API и получить компактный результат."""
            return self.run(tool_name, max_examples)

        return [run_api_checks]

    def outcome(self) -> dict:
        states = [t.get("state") for r in self.runs for t in r.get("tools", [])]
        failed_run = any(r.get("state") != "completed" for r in self.runs)
        execution_state = (
            "not_run"
            if not self.runs
            else "error"
            if failed_run or any(s in {"error", "cancelled"} for s in states)
            else "failed"
            if "failed" in states
            else "passed"
            if states and all(s == "passed" for s in states)
            else "unknown"
        )
        return {
            "tool_mode": "external",
            "tool_runs": self.runs,
            "tool_artifacts": self.artifacts,
            "metrics": {
                "tool_runs": [
                    {"run_id": r.get("run_id"), "metrics": r.get("metrics")}
                    for r in self.runs
                ]
            }
            if self.runs
            else None,
            "execution": {
                "kind": "tools",
                "state": execution_state,
                "runs": [
                    {
                        "run_id": r.get("run_id"),
                        "state": r.get("state"),
                        "tools": [
                            {k: t[k] for k in ("tool", "state", "error") if k in t}
                            for t in r.get("tools", [])
                        ],
                    }
                    for r in self.runs
                ],
            },
        }
