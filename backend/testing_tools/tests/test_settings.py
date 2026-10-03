from testing_tools.api.schemas import ToolName
from testing_tools.app.settings import Settings


def test_settings_defaults() -> None:
    s = Settings()

    assert s.api_prefix == "/api/v0"
    assert s.max_active_runs == 16
    assert s.unavailable_tools == frozenset()


def test_settings_from_env() -> None:
    s = Settings.from_env(
        {
            "TT_API_PREFIX": "/api/v1",
            "TT_TOOL_DELAY_S": "0.25",
            "TT_MAX_ACTIVE_RUNS": "3",
            "TT_UNAVAILABLE_TOOLS": "microcks, mutation",
        }
    )

    assert s.api_prefix == "/api/v1"
    assert s.tool_delay_s == 0.25
    assert s.max_active_runs == 3
    assert s.unavailable_tools == frozenset({ToolName.MICROCKS, ToolName.MUTATION})
