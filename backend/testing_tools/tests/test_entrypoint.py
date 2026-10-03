from typing import Any

import pytest
import uvicorn

import testing_tools


def test_main_serves_app_factory_on_env_address(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setenv("TT_HOST", "0.0.0.0")
    monkeypatch.setenv("TT_PORT", "9000")

    testing_tools.main()

    [(args, kwargs)] = calls
    assert args == ("testing_tools.app.main:create_app",)
    assert kwargs["factory"] is True
    assert (kwargs["host"], kwargs["port"]) == ("0.0.0.0", 9000)


def test_main_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    monkeypatch.delenv("TT_HOST", raising=False)
    monkeypatch.delenv("TT_PORT", raising=False)

    testing_tools.main()

    assert (calls[0]["host"], calls[0]["port"]) == ("127.0.0.1", 8000)
