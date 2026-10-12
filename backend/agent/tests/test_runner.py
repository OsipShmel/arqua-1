import json
import subprocess

import pytest

from agent import runner


@pytest.mark.parametrize(
    "exception", [subprocess.TimeoutExpired("worker", 1), KeyboardInterrupt()]
)
def test_runner_kills_and_reaps_worker_on_timeout_or_interrupt(monkeypatch, exception):
    calls = []

    class Process:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, *args, **kwargs):
            calls.append("communicate")
            if len(calls) == 1:
                raise exception
            return "", None

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: Process())
    expected = (
        TimeoutError
        if isinstance(exception, subprocess.TimeoutExpired)
        else KeyboardInterrupt
    )
    with pytest.raises(expected):
        runner.invoke_agent("task", timeout=1, max_steps=3)
    assert calls == ["communicate", "kill", "communicate"]


def test_runner_task_is_sent_as_utf8_stdin(monkeypatch):
    received = {}

    class Process:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, data, *, timeout):
            received["request"] = json.loads(data)
            return '{"status":"success","output":"OK"}', None

    def launch(command, **kwargs):
        received["command"] = command
        return Process()

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    assert (
        runner.invoke_agent("Русский контракт", timeout=1, max_steps=3)["status"]
        == "success"
    )
    assert received["request"] == {"task": "Русский контракт", "max_steps": 3}
    assert "Русский контракт" not in str(received["command"])
