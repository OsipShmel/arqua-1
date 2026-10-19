"""Real CLI + LangChain + OpenAI client against a local scripted provider."""

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Offline Pets", "version": "1"},
    "paths": {"/pets": {"get": {"responses": {"200": {"description": "OK"}}}}},
}


@contextmanager
def scripted_provider(mode="normal"):
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, document, status=200):
            content = json.dumps(document).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # An agent deadline may close the client connection.

        def do_GET(self):
            requests.append(("GET", self.path, None))
            self.send_json(SPEC)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", self.path, body))
            if mode == "stall":
                release.wait(timeout=15)
            if mode == "reject":
                self.send_json(
                    {"error": {"message": "Rejected local-test-key"}}, status=401
                )
                return
            used_tool = any(message["role"] == "tool" for message in body["messages"])
            message = {
                "role": "assistant",
                "content": "Проверена интеграция tools API."
                if mode == "gateway"
                else "Проверена интеграция. Режим DEMO.",
            }
            reason = "stop"
            if not used_tool or mode == "loop":
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_health",
                            "type": "function",
                            "function": {
                                "name": "run_api_checks"
                                if mode == "gateway"
                                else "check_api_health",
                                "arguments": json.dumps(
                                    {"tool_name": "schemathesis"}
                                    if mode == "gateway"
                                    else {"url": "http://target.invalid"}
                                ),
                            },
                        }
                    ],
                }
                reason = "tool_calls"
            self.send_json(
                {
                    "id": "chatcmpl-local-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "qa-test-model",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": reason}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def isolated_env():
    env = os.environ.copy()
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "DEEPCODE_API_KEY",
        "DEEPCODE_BASE_URL",
        "DEEPCODE_MODEL",
        "DEEPCODE_TIMEOUT",
        "DEEPCODE_TEMPERATURE",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    ):
        env.pop(key, None)
    env.update(
        {
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return env


@pytest.mark.parametrize("source_kind", ["file", "url"])
def test_full_cli_and_real_agent_tool_loop(tmp_path, source_kind):
    with scripted_provider() as (url, requests):
        env = isolated_env()
        env.update(
            {
                "DEEPCODE_API_KEY": "local-test-key",
                "DEEPCODE_BASE_URL": url + "/v1",
                "DEEPCODE_MODEL": "qa-test-model",
            }
        )
        source = tmp_path / "контракт с пробелами.json"
        source.write_text(json.dumps(SPEC), encoding="utf-8")
        contract = str(source) if source_kind == "file" else url + "/openapi"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent",
                "generate",
                contract,
                "--base-url",
                "http://target.invalid",
                "--output",
                str(tmp_path / "runs"),
                "--format",
                "json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["agent"]["output"] == "Проверена интеграция. Режим DEMO."
    assert report["status"] == "success" and report["metrics"] is None
    posts = [body for method, path, body in requests if method == "POST"]
    assert len(posts) == 2
    assert all(
        path == "/v1/chat/completions"
        for method, path, _ in requests
        if method == "POST"
    )
    assert any(
        message["role"] == "tool" and "[DEMO]" in message["content"]
        for message in posts[-1]["messages"]
    )
    assert any(
        "Offline Pets" in str(message["content"]) for message in posts[0]["messages"]
    )
    assert len([item for item in requests if item[0] == "GET"]) == (
        source_kind == "url"
    )
    assert Path(report["artifacts"]["json"]).is_file()
    assert Path(report["artifacts"]["markdown"]).is_file()
    assert report["runtime"]["llm_calls"] == 2
    assert report["runtime"]["total_tokens"] == 30
    assert report["runtime"]["usage_complete"] is True
    assert report["runtime"]["prompt"]["sha256"]
    for artifact in ("manifest", "events", "runtime", "summary"):
        assert "local-test-key" not in Path(report["artifacts"][artifact]).read_text(
            encoding="utf-8"
        )


@pytest.mark.parametrize(
    "arguments", [["--help"], ["--version"], ["generate", "--help"]]
)
def test_installed_command_help_without_provider_settings(tmp_path, arguments):
    command = Path(sys.executable).with_name(
        "arqa-testgen.exe" if os.name == "nt" else "arqa-testgen"
    )
    process = subprocess.run(
        [str(command), *arguments],
        cwd=tmp_path,
        env=isolated_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert process.returncode == 0, process.stderr
    assert "arqa-testgen" in process.stdout


@pytest.mark.parametrize(
    "mode,flags,code,error_type",
    [
        ("stall", ["--agent-timeout", "3"], 124, "TimeoutError"),
        ("loop", ["--max-steps", "3"], 1, "GraphRecursionError"),
        ("reject", [], 1, "AuthenticationError"),
    ],
)
def test_real_agent_failure_limits_and_secret_redaction(
    tmp_path, mode, flags, code, error_type
):
    source = tmp_path / "contract.json"
    source.write_text(json.dumps(SPEC), encoding="utf-8")
    with scripted_provider(mode) as (url, requests):
        env = isolated_env()
        env.update(
            {
                "DEEPCODE_API_KEY": "local-test-key",
                "DEEPCODE_BASE_URL": url + "/v1",
                "DEEPCODE_MODEL": "qa-test-model",
            }
        )
        started = time.monotonic()
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent",
                "generate",
                str(source),
                "--base-url",
                "http://target.invalid",
                "--format",
                "json",
                "--output",
                str(tmp_path / "runs"),
                *flags,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        elapsed = time.monotonic() - started
    assert process.returncode == code, process.stderr
    report = json.loads(process.stdout)
    assert report["error"]["type"].endswith(error_type)
    assert "local-test-key" not in process.stdout + process.stderr
    assert "Traceback" not in process.stderr
    assert (
        json.loads(Path(report["artifacts"]["json"]).read_text(encoding="utf-8"))
        == report
    )
    if mode == "stall":
        assert report["status"] == "timeout"
        assert elapsed < 10  # Must stop before the provider releases its response.
    if mode == "loop":
        assert 0 < len([r for r in requests if r[0] == "POST"]) <= 3


def test_installed_doctor_never_calls_provider(tmp_path):
    with scripted_provider() as (url, requests):
        env = isolated_env()
        env.update(
            {
                "DEEPCODE_API_KEY": "local-test-key",
                "DEEPCODE_BASE_URL": url + "/v1",
                "DEEPCODE_MODEL": "qa-test-model",
            }
        )
        process = subprocess.run(
            [sys.executable, "-m", "agent", "doctor", "--format", "json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["status"] == "ok"
    assert "local-test-key" not in process.stdout + process.stderr
    assert requests == []


@contextmanager
def local_tools_api():
    requests = []
    run_id = "11111111-1111-4111-8111-111111111111"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, document, status=200):
            content = json.dumps(document).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, document))
            self.send_json({"run_id": run_id}, 202)

        def do_GET(self):
            if self.path.endswith("/result"):
                self.send_json(
                    {
                        "run_id": run_id,
                        "state": "completed",
                        "metrics": {"contract_coverage": 1},
                        "tools": [
                            {
                                "tool": "schemathesis",
                                "state": "failed",
                                "summary": {"failures": 1},
                                "artifacts": [
                                    {
                                        "name": "junit.xml",
                                        "media_type": "application/xml",
                                    }
                                ],
                            }
                        ],
                    }
                )
            elif self.path.endswith("/junit.xml"):
                content = b'<testsuites><testsuite failures="1"/></testsuites>'
                self.send_response(200)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_json({"state": "completed"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_full_cli_source_agent_gateway_and_artifacts(tmp_path):
    source = tmp_path / "service"
    source.mkdir()
    (source / "app.py").write_text("def calculate(value):\n    return value + 1\n")
    document = {**SPEC, "x-private-unused": "DO-NOT-FORWARD-RAW"}
    contract = tmp_path / "openapi.json"
    contract.write_text(json.dumps(document), encoding="utf-8")
    with (
        scripted_provider("gateway") as (provider, llm_requests),
        local_tools_api() as (tools, tool_requests),
    ):
        env = isolated_env()
        env.update(
            DEEPCODE_API_KEY="local-test-key",
            DEEPCODE_BASE_URL=provider + "/v1",
            DEEPCODE_MODEL="qa-test-model",
        )
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent",
                "generate",
                str(contract),
                "--base-url",
                "http://target.invalid",
                "--tools-url",
                tools,
                "--source-dir",
                str(source),
                "--format",
                "json",
                "--output",
                str(tmp_path / "runs"),
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["status"] == "success" and report["execution"]["state"] == "failed"
    assert report["tool_mode"] == "external" and report["generated_tests"] == []
    assert report["runtime"]["total_tokens"] == 30
    assert len(report["tool_artifacts"]) == 1
    assert (
        Path(report["tool_artifacts"][0]["path"])
        .read_bytes()
        .startswith(b"<testsuites>")
    )
    assert tool_requests[0][0] == "/api/v1/runs"
    assert tool_requests[0][1]["labels"]["arqa_run_id"] == report["run_id"]
    assert "DO-NOT-FORWARD-RAW" in tool_requests[0][1]["contract"]["content"]
    user_messages = str(llm_requests[0][2]["messages"])
    assert "calculate" in user_messages and "DO-NOT-FORWARD-RAW" not in user_messages
    events = [
        json.loads(line)
        for line in Path(report["artifacts"]["events"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {
        "llm.started",
        "llm.finished",
        "tool.started",
        "tool.finished",
        "gateway.started",
        "gateway.finished",
    } <= {e["event"] for e in events}
    assert "local-test-key" not in process.stdout + Path(
        report["artifacts"]["events"]
    ).read_text(encoding="utf-8")
