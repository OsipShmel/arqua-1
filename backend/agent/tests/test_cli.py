import json
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
    import agent.agent

    tasks = []

    def fake_agent(task):
        tasks.append(task)
        print("tool diagnostic on stdout")
        return {
            "status": "success",
            "output": [{"type": "text", "text": "Ответ агента"}],
        }

    monkeypatch.setattr(agent.agent, "run_qa_agent", fake_agent)
    assert cli.main(arguments) == 0
    first_output = capsys.readouterr()
    first = json.loads(first_output.out)
    assert "tool diagnostic" in first_output.err
    assert first["metrics"] is None and first["tool_mode"] == "demo"
    assert first["contract"]["operations_total"] == 1
    assert '"path":"/pets"' in tasks[0] and '"raw"' not in tasks[0]
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
    import agent.agent

    monkeypatch.setattr(
        agent.agent, "run_qa_agent", lambda _: pytest.fail("Agent must not run")
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
    ],
)
def test_agent_failure_and_interrupt(
    arguments, monkeypatch, capsys, exception, code, status
):
    import agent.agent

    def failing_agent(task):
        raise exception

    monkeypatch.setattr(agent.agent, "run_qa_agent", failing_agent)
    assert cli.main(arguments) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["error"] is not None
    assert Path(result["artifacts"]["markdown"]).is_file()


def test_error_redacts_provider_key(arguments, monkeypatch, capsys):
    import agent.agent

    monkeypatch.setenv("DEEPCODE_API_KEY", "secret-probe-key")

    def failing_agent(task):
        raise RuntimeError("provider rejected secret-probe-key")

    monkeypatch.setattr(agent.agent, "run_qa_agent", failing_agent)
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert "secret-probe-key" not in captured.out + captured.err
    result = json.loads(captured.out)
    assert "secret-probe-key" not in Path(result["artifacts"]["json"]).read_text()


def test_error_result_from_agent_is_not_success(arguments, monkeypatch, capsys):
    import agent.agent

    monkeypatch.setattr(
        agent.agent,
        "run_qa_agent",
        lambda _: {"status": "error", "output": "Не удалось выполнить задание"},
    )
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "error"
    assert "Не удалось" in captured.err


def test_report_failure_returns_nonzero(arguments, monkeypatch, capsys):
    import agent.agent

    monkeypatch.setattr(
        agent.agent, "run_qa_agent", lambda _: {"status": "success", "output": "OK"}
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
