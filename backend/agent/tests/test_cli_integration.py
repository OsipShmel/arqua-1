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
                "content": "Проверена интеграция. Режим DEMO.",
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
                                "name": "check_api_health",
                                "arguments": json.dumps(
                                    {"url": "http://target.invalid"}
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
