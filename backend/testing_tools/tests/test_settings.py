from pathlib import Path

import pytest

from testing_tools.api.schemas import ToolName
from testing_tools.app.settings import Settings

ENV_EXAMPLE = Path(__file__).parents[1] / ".env.example"


def test_settings_defaults() -> None:
    s = Settings()

    assert (s.host, s.port) == ("127.0.0.1", 8000)
    assert s.api_prefix == "/api/v0"
    assert s.tool_delay_s == 1.0
    assert s.max_active_runs == 16
    assert s.unavailable_tools == frozenset()


def test_settings_from_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_API_PREFIX", "/api/v1")
    monkeypatch.setenv("TT_TOOL_DELAY_S", "0.25")
    monkeypatch.setenv("TT_MAX_ACTIVE_RUNS", "3")
    monkeypatch.setenv("TT_UNAVAILABLE_TOOLS", "microcks, mutation")

    s = Settings()

    assert s.api_prefix == "/api/v1"
    assert s.tool_delay_s == 0.25
    assert s.max_active_runs == 3
    assert s.unavailable_tools == frozenset({ToolName.MICROCKS, ToolName.MUTATION})


def test_settings_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("TT_PORT=9100\nTT_UNAVAILABLE_TOOLS=schemathesis\n")
    monkeypatch.chdir(tmp_path)

    s = Settings()

    assert s.port == 9100
    assert s.unavailable_tools == frozenset({ToolName.SCHEMATHESIS})


def test_process_env_beats_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("TT_PORT=9100\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TT_PORT", "9200")

    assert Settings().port == 9200


def test_empty_unavailable_tools_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_UNAVAILABLE_TOOLS", "")

    assert Settings().unavailable_tools == frozenset()


def test_unknown_tool_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_UNAVAILABLE_TOOLS", "restler")

    with pytest.raises(ValueError):
        Settings()


def test_env_example_lists_exactly_the_settings() -> None:
    keys = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    configurable = set(Settings.model_fields) - {"version"}
    assert keys == {f"TT_{name.upper()}" for name in configurable}


def test_env_example_loads_into_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(ENV_EXAMPLE.read_text())
    monkeypatch.chdir(tmp_path)

    assert Settings() == Settings(_env_file=None)
