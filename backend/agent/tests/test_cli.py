import json
import sys
from pathlib import Path

import pytest

from agent import cli

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Test API", "version": "1"},
    "paths": {"/pets": {"get": {"responses": {"200": {"description": "OK"}}}}},
}


@pytest.fixture
def arguments(tmp_path):
    contract = tmp_path / "контракт с пробелами.json"
    contract.write_text(json.dumps(SPEC), encoding="utf-8")
    return [
        "generate",
        str(contract),
        "--base-url",
        "http://localhost:8080",
        "--output",
        str(tmp_path / "runs"),
        "--format",
        "json",
    ]


def test_cli_preserves_json_stdout_and_saves_separate_reports(
    arguments, monkeypatch, capsys
):
    tasks = []

    def fake_agent(task, **limits):
        tasks.append(task)
        print("tool diagnostic", file=sys.stderr)
        return {
            "status": "success",
            "output": [{"type": "text", "text": "Ответ агента"}],
            "tool_mode": "demo",
        }

    monkeypatch.setattr(cli, "invoke_agent", fake_agent)
    assert cli.main(arguments) == 0
    first_output = capsys.readouterr()
    first = json.loads(first_output.out)
    assert "tool diagnostic" in first_output.err
    assert first["metrics"] is None and first["tool_mode"] == "demo"
    assert first["contract"]["operations_total"] == 1
    assert '"/pets"' in tasks[0] and '"raw"' not in tasks[0]
    assert "http://localhost:8080" in tasks[0]
    assert (
        json.loads(Path(first["artifacts"]["json"]).read_text(encoding="utf-8"))
        == first
    )
    assert "Ответ агента" in Path(first["artifacts"]["markdown"]).read_text(
        encoding="utf-8"
    )
    assert cli.main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["run_id"] != second["run_id"]
    assert Path(first["artifacts"]["json"]).is_file()


def test_input_failure_does_not_call_agent(arguments, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "invoke_agent", lambda _, **limits: pytest.fail("Agent must not run")
    )
    arguments[1] += ".missing"
    assert cli.main(arguments) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and "не найден" in result["error"]["message"]
    assert Path(result["artifacts"]["json"]).is_file()


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (RuntimeError("missing settings"), 1, "error"),
        (KeyboardInterrupt(), 130, "interrupted"),
        (TimeoutError("agent deadline"), 124, "timeout"),
    ],
)
def test_agent_failure_and_interrupt(
    arguments, monkeypatch, capsys, exception, code, status
):
    def failing_agent(task, **limits):
        raise exception

    monkeypatch.setattr(cli, "invoke_agent", failing_agent)
    assert cli.main(arguments) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["error"] is not None
    assert Path(result["artifacts"]["markdown"]).is_file()


def test_error_redacts_provider_key(arguments, monkeypatch, capsys):
    monkeypatch.setenv("DEEPCODE_API_KEY", "secret-probe-key")

    def failing_agent(task, **limits):
        raise RuntimeError("provider rejected secret-probe-key")

    monkeypatch.setattr(cli, "invoke_agent", failing_agent)
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert "secret-probe-key" not in captured.out + captured.err
    result = json.loads(captured.out)
    assert "secret-probe-key" not in Path(result["artifacts"]["json"]).read_text(
        encoding="utf-8"
    )


def test_error_result_from_agent_is_not_success(arguments, monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "invoke_agent",
        lambda _, **limits: {
            "status": "error",
            "output": "Не удалось выполнить задание",
        },
    )
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "error"
    assert "Не удалось" in captured.err


def test_report_failure_returns_nonzero(arguments, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "invoke_agent", lambda _, **limits: {"status": "success", "output": "OK"}
    )

    def cannot_write(*args):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "write_reports", cannot_write)
    assert cli.main(arguments) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error" and report["artifacts"] == {}


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_rejected(arguments, capsys, value):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments + ["--contract-timeout", value])
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "value", ["ftp://server", "http://server:bad", "http://server:0"]
)
def test_invalid_target_rejected(arguments, capsys, value):
    arguments[arguments.index("--base-url") + 1] = value
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_agent_timeout_rejected(arguments, value):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments + ["--agent-timeout", value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "nan"])
def test_invalid_step_limit_rejected(arguments, value):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments + ["--max-steps", value])
    assert error.value.code == 2


def test_limits_forwarded_and_recorded(arguments, monkeypatch, capsys):
    received = {}

    def agent(task, **limits):
        received.update(limits)
        return {"status": "success", "output": "OK"}

    monkeypatch.setattr(cli, "invoke_agent", agent)
    assert cli.main(arguments + ["--agent-timeout", "7.5", "--max-steps", "3"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert received["timeout"] == 7.5 and received["max_steps"] == 3
    assert received["context"]["run_id"] == report["run_id"]
    assert report["limits"] == {"agent_timeout": 7.5, "max_steps": 3}


def test_source_manifest_events_and_agent_files(
    arguments, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "service"
    source.mkdir()
    (source / "app.py").write_text("def calculate(x):\n    return x + 1\n")

    def agent(task, **kwargs):
        assert "calculate" in task and "source_context_json" in task
        return {
            "status": "success",
            "output": "done",
            "tool_mode": "real",
            "metrics": {"tests_total": 1},
            "execution": {"state": "passed"},
            "files": [
                {
                    "path": "tests/test_calc.py",
                    "kind": "pytest",
                    "content": "def test_calc():\n    assert 1 == 1\n",
                },
                {"path": "junit.xml", "kind": "junit", "content": "<testsuites/>"},
            ],
        }

    monkeypatch.setattr(cli, "invoke_agent", agent)
    assert cli.main(arguments + ["--source-dir", str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 2 and report["tool_mode"] == "real"
    assert len(report["generated_tests"]) == 1
    assert Path(report["generated_tests"][0]).is_file()
    assert report["metrics"] == {"tests_total": 1}
    manifest = json.loads(
        Path(report["artifacts"]["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["source"]["files_total"] == 1
    assert manifest["environment"]["packages"]["agent"] == "0.1.0"
    events = [
        json.loads(line)
        for line in Path(report["artifacts"]["events"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[0]["event"] == "run.started" and events[-1]["event"] == "run.finished"
    assert {e["run_id"] for e in events} == {report["run_id"]}


def test_invalid_source_never_invokes_agent(arguments, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "invoke_agent", lambda *a, **k: pytest.fail("Agent must not run")
    )
    assert cli.main(arguments + ["--source-dir", "nonexistent-arqa-source"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error" and report["source"] is None
    assert Path(report["artifacts"]["manifest"]).exists()


def test_tools_options_are_passed_only_to_agent(arguments, monkeypatch, capsys):
    captured = {}

    def agent(task, **kwargs):
        captured.update(kwargs["context"]["gateway"])
        assert '"paths"' not in task  # Raw contract is for deterministic tools only.
        return {"status": "success", "output": "done", "tool_mode": "external"}

    monkeypatch.setattr(cli, "invoke_agent", agent)
    assert (
        cli.main(
            arguments
            + ["--tools-url", "http://tools.test/api/v1", "--tools", "microcks"]
        )
        == 0
    )
    assert captured["tools"] == ["microcks"] and "paths" in captured["contract"]
    assert json.loads(capsys.readouterr().out)["tool_mode"] == "external"
