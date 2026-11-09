"""Shared stand for the ПМИ control examples (А-1…А-25).

Everything runs on loopback inside the test process: a scripted OpenAI-compatible LLM,
the demo services from `demo/`, the testing_tools gateway, and the real `arqa-testgen` CLI
as a subprocess. Tests marked `llm` use the model configured in `DEEPCODE_*` instead.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"

Message = dict[str, Any]
Step = Callable[[dict[str, Any]], Message]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "llm: needs a real model from DEEPCODE_* (ARQA_LLM_TESTS=1)"
    )
    config.addinivalue_line(
        "markers", "docker: needs a running Microcks (ARQA_MICROCKS_URL)"
    )
    config.addinivalue_line(
        "markers", "ollama: needs a local Ollama model (ARQA_OLLAMA_MODEL)"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    llm = os.environ.get("ARQA_LLM_TESTS") == "1"
    microcks = bool(os.environ.get("ARQA_MICROCKS_URL"))
    ollama = bool(os.environ.get("ARQA_OLLAMA_MODEL"))
    for item in items:
        if "ollama" in item.keywords and not ollama:
            item.add_marker(
                pytest.mark.skip(reason="local model: set ARQA_OLLAMA_MODEL")
            )
        if "llm" in item.keywords and not llm:
            item.add_marker(
                pytest.mark.skip(reason="real LLM: set ARQA_LLM_TESTS=1 and DEEPCODE_*")
            )
        if "docker" in item.keywords and not microcks:
            item.add_marker(pytest.mark.skip(reason="Microcks: set ARQA_MICROCKS_URL"))


# --------------------------------------------------------------------------- #
# Scripted LLM
# --------------------------------------------------------------------------- #


def tool_call(name: str, **arguments: Any) -> Step:
    def step(_: dict[str, Any]) -> Message:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }

    return step


def answer(text: str) -> Step:
    return lambda _: {"role": "assistant", "content": text}


@dataclass
class ScriptedLLM:
    """OpenAI-compatible chat endpoint that plays `steps` in order and records requests."""

    url: str
    requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return self.url + "/v1"

    def messages(self, index: int = -1) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = self.requests[index]["messages"]
        return messages

    def sent_text(self) -> str:
        """Everything the model was ever sent, as one string."""
        return json.dumps(self.requests, ensure_ascii=False)

    def offered_tools(self) -> list[str]:
        return [t["function"]["name"] for t in self.requests[0].get("tools", [])]

    def tool_results(self) -> list[str]:
        return [m["content"] for m in self.messages() if m["role"] == "tool"]


@contextmanager
def scripted_llm(
    *steps: Step, final: str = "Готово.", status: int = 200, delay: float = 0
) -> Iterator[ScriptedLLM]:
    llm = ScriptedLLM(url="")
    plan = list(steps)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            llm.requests.append(body)
            if delay:
                time.sleep(delay)
            if status != 200:
                self._send({"error": {"message": "scripted failure"}}, status)
                return
            message = plan.pop(0)(body) if plan else answer(final)(body)
            reason = "tool_calls" if message.get("tool_calls") else "stop"
            self._send(
                {
                    "id": "chatcmpl-acceptance",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body.get("model", "scripted"),
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": reason}
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                },
                200,
            )

        def _send(self, document: dict[str, Any], code: int) -> None:
            content = json.dumps(document).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    llm.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield llm
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def static_server(routes: dict[str, tuple[str, bytes]]) -> Iterator[str]:
    """Serve fixed `path -> (content type, body)`; anything else is 404."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            content_type, body = routes.get(self.path, ("text/plain", b""))
            self.send_response(200 if self.path in routes else 404)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# ASGI services on loopback
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@contextmanager
def serve(app: Any, host: str = "127.0.0.1") -> Iterator[str]:
    """Run an ASGI app on a free port; `host="0.0.0.0"` makes it reachable from Docker."""
    listener = socket.socket()
    listener.bind((host, 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("service did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()


def port_of(url: str) -> int:
    return int(url.rsplit(":", 1)[1].split("/")[0])


def demo_app(name: str) -> Any:
    """A fresh instance of `demo/<name>/app.py` (its in-memory state starts clean)."""
    if str(DEMO) not in sys.path:
        sys.path.insert(0, str(DEMO))
    spec = importlib.util.spec_from_file_location(
        f"demo_{name}_{time.monotonic_ns()}", DEMO / name / "app.py"
    )
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@contextmanager
def demo_service(name: str = "petstore") -> Iterator[str]:
    with serve(demo_app(name)) as url:
        yield url


@contextmanager
def tools_service(tmp_path: Path, **settings: Any) -> Iterator[str]:
    """testing_tools gateway; yields the API base URL (…/api/v1)."""
    from testing_tools.app.main import create_app
    from testing_tools.app.settings import Settings

    scratch = tmp_path / "tt-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "results_dir": str(tmp_path / "tt-results"),
        "work_dir": str(scratch),
        "tool_mode": "real",
    } | settings
    with serve(create_app(Settings(_env_file=None, **options))) as url:
        yield url + "/api/v1"


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #

PROVIDER_VARS = (
    "DEEPCODE_API_KEY",
    "DEEPCODE_BASE_URL",
    "DEEPCODE_MODEL",
    "DEEPCODE_TIMEOUT",
    "DEEPCODE_TEMPERATURE",
)


def cli_env(llm: ScriptedLLM | None = None, **extra: str) -> dict[str, str]:
    """Environment for the CLI: scripted provider, or the real one when `llm` is None."""
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    env |= {
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "NO_PROXY": "127.0.0.1,localhost",
        "PYTHONUTF8": "1",
    }
    if llm is not None:
        for key in PROVIDER_VARS:
            env.pop(key, None)
        env |= {
            "DEEPCODE_API_KEY": "acceptance-test-key",
            "DEEPCODE_BASE_URL": llm.base_url,
            "DEEPCODE_MODEL": "scripted-model",
        }
    return env | extra


@dataclass
class CliRun:
    code: int
    stdout: str
    stderr: str

    @property
    def report(self) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(self.stdout)
        return report

    @property
    def run_dir(self) -> Path:
        return Path(self.report["artifacts"]["json"]).parent


def run_cli(*args: str, env: dict[str, str], cwd: Path, timeout: float = 120) -> CliRun:
    """`arqa-testgen <args>` in an empty working directory, so no stray .env is read."""
    process = subprocess.run(
        [sys.executable, "-m", "agent", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    return CliRun(process.returncode, process.stdout, process.stderr)


def generate(
    contract: str | Path,
    base_url: str,
    *,
    env: dict[str, str],
    cwd: Path,
    extra: tuple[str, ...] = (),
    timeout: float = 120,
) -> CliRun:
    return run_cli(
        "generate",
        str(contract),
        "--base-url",
        base_url,
        "--output",
        str(cwd / "runs"),
        "--format",
        "json",
        *extra,
        env=env,
        cwd=cwd,
        timeout=timeout,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


PETSTORE_YAML = DEMO / "petstore" / "openapi.yaml"
ORDERS_YAML = DEMO / "orders" / "openapi.yaml"


# --------------------------------------------------------------------------- #
# testing_tools API
# --------------------------------------------------------------------------- #


def tools_run(
    tools_url: str, payload: dict[str, Any], timeout: float = 300
) -> dict[str, Any]:
    """POST a run, poll it to a terminal state, return its result."""
    import httpx

    with httpx.Client(base_url=tools_url + "/", timeout=30) as client:
        created = client.post("runs", json=payload)
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        deadline = time.monotonic() + timeout
        while client.get(f"runs/{run_id}").json()["state"] in {"queued", "running"}:
            assert time.monotonic() < deadline, f"run {run_id} did not finish"
            time.sleep(0.2)
        result: dict[str, Any] = client.get(f"runs/{run_id}/result").json()
        return result


def artifact(tools_url: str, result: dict[str, Any], name: str) -> bytes:
    import httpx

    response = httpx.get(
        f"{tools_url}/runs/{result['run_id']}/artifacts/{name}", timeout=30
    )
    response.raise_for_status()
    return response.content


def inline_contract(path: Path) -> dict[str, Any]:
    return {
        "kind": "inline",
        "format": "yaml",
        "content": path.read_text(encoding="utf-8"),
    }


def inline_suite(files: dict[str, str]) -> dict[str, Any]:
    return {"kind": "inline", "files": files}


# --------------------------------------------------------------------------- #
# Generated suites (the generator is not implemented yet; see А-4)
# --------------------------------------------------------------------------- #


def generated_suite(run: CliRun) -> list[Path]:
    """pytest files the CLI saved from the agent's result."""
    return [Path(p) for p in run.report["generated_tests"]]


def collect_only(files: list[Path], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *map(str, files)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
