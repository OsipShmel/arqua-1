import json
from types import SimpleNamespace

from agent.events import Journal
from agent.telemetry import RunTelemetry


def test_usage_is_provider_reported_and_incomplete_calls_remain_visible(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPCODE_API_KEY", "hidden-provider-key")
    journal = Journal(tmp_path / "events.jsonl", "run-1")
    telemetry = RunTelemetry(journal, tmp_path / "runtime.json")
    telemetry.on_chat_model_start({}, [], run_id="one")
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(
                        usage_metadata={
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        }
                    )
                )
            ]
        ],
        llm_output=None,
    )
    telemetry.on_llm_end(response, run_id="one")
    assert telemetry.state["usage_complete"] is True
    telemetry.on_chat_model_start({}, [], run_id="two")
    assert (
        json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))[
            "usage_complete"
        ]
        is False
    )
    telemetry.on_llm_error(RuntimeError("hidden-provider-key rejected"), run_id="two")
    assert telemetry.state["total_tokens"] == 15 and telemetry.state["llm_calls"] == 2
    assert telemetry.state["cost"] is None
    assert "hidden-provider-key" not in (tmp_path / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_missing_usage_is_not_fabricated(tmp_path):
    telemetry = RunTelemetry(Journal(tmp_path / "events.jsonl", "run"))
    telemetry.on_chat_model_start({}, [], run_id="one")
    telemetry.on_llm_end(SimpleNamespace(generations=[], llm_output={}), run_id="one")
    assert (
        telemetry.state["usage_reports"] == 0
        and telemetry.state["usage_complete"] is False
    )
    assert telemetry.state["total_tokens"] is None
