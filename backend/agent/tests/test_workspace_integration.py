"""Installed CLI and team gateway with real Schemathesis; only the LLM is scripted."""

import json
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_cli_integration import SPEC, isolated_env, scripted_provider


@contextmanager
def gateway_server(tmp_path):
    pytest.importorskip("testing_tools", reason="Install the full uv workspace")
    import uvicorn
    from testing_tools.app.main import create_app
    from testing_tools.app.settings import Settings

    (tmp_path / "scratch").mkdir()
    app = create_app(
        Settings(
            _env_file=None,
            tool_mode="real",
            results_dir=str(tmp_path / "archive"),
            work_dir=str(tmp_path / "scratch"),
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    pytest.fail("Team gateway did not start")
                time.sleep(0.02)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/api/v1"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Team gateway did not stop"


@contextmanager
def broken_api():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            content = b'{"id":"wrong-type"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cli_official_parser_agent_and_real_team_gateway(tmp_path, monkeypatch):
    # Schemathesis prints Unicode; Windows must not inherit a legacy code page.
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    contract = json.loads(json.dumps(SPEC))
    contract["paths"]["/pets"]["get"]["responses"]["200"]["content"] = {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            }
        }
    }
    source = tmp_path / "openapi.json"
    source.write_text(json.dumps(contract), encoding="utf-8")
    with (
        gateway_server(tmp_path) as tools_url,
        broken_api() as target_url,
        scripted_provider("gateway") as (model_url, requests),
    ):
        env = isolated_env()
        env.update(
            DEEPCODE_API_KEY="local-test-key",
            DEEPCODE_BASE_URL=model_url + "/v1",
            DEEPCODE_MODEL="scripted-local-provider",
        )
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent",
                "generate",
                str(source),
                "--base-url",
                target_url,
                "--tools-url",
                tools_url,
                "--tools-timeout",
                "60",
                "--agent-timeout",
                "90",
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
            timeout=100,
        )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["execution"]["state"] == "failed"  # A real API defect.
    tool_result = report["tool_runs"][0]["tools"][0]
    assert tool_result["tool"] == "schemathesis"
    assert tool_result["tool_version"]
    assert any(
        f["check"] == "response_schema_conformance" for f in tool_result["findings"]
    )
    assert any(Path(a["path"]).suffix == ".xml" for a in report["tool_artifacts"])
    assert report["runtime"]["llm_calls"] == 2
    assert any(
        m["role"] == "tool" and "response_schema_conformance" in m["content"]
        for method, _, body in requests
        if method == "POST"
        for m in body["messages"]
    )
