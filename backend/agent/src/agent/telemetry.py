"""LangChain callbacks; persisted between calls so timeouts retain partial usage."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

from langchain_core.callbacks import BaseCallbackHandler

from agent.events import Journal, atomic_json


class RunTelemetry(BaseCallbackHandler):
    def __init__(self, journal: Journal, snapshot_path: Path | None = None):
        self.journal = journal
        self.snapshot_path = snapshot_path
        self.state = {
            "model": None,
            "prompt": None,
            "llm_calls": 0,
            "llm_errors": 0,
            "usage_reports": 0,
            "usage_complete": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost": None,
        }
        self.started: dict = {}
        self.totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def persist(self) -> None:
        for key, value in self.totals.items():
            self.state[key] = (
                value
                if self.state["usage_reports"] or self.state["llm_calls"] == 0
                else None
            )
        self.state["usage_complete"] = (
            self.state["usage_reports"] == self.state["llm_calls"]
            and self.state["llm_errors"] == 0
        )
        if self.snapshot_path:
            atomic_json(self.snapshot_path, self.state)

    def configure(self, *, model: dict, prompt: dict) -> None:
        with self.journal.lock:
            self.state.update(model=model, prompt=prompt)
            self.persist()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        with self.journal.lock:
            self.started[str(run_id)] = monotonic()
            self.state["llm_calls"] += 1
            self.journal.emit("llm.started", call_id=str(run_id))
            self.persist()

    def on_llm_end(self, response, *, run_id, **kwargs):
        with self.journal.lock:
            usage = None
            for batch in response.generations:
                for generation in batch:
                    candidate = getattr(
                        getattr(generation, "message", None), "usage_metadata", None
                    )
                    if candidate:
                        usage = {
                            k: candidate.get(k)
                            for k in ("input_tokens", "output_tokens", "total_tokens")
                        }
                        break
                if usage:
                    break
            if not usage:
                raw = (response.llm_output or {}).get("token_usage") or {}
                if raw:
                    usage = {
                        "input_tokens": raw.get("prompt_tokens"),
                        "output_tokens": raw.get("completion_tokens"),
                        "total_tokens": raw.get("total_tokens"),
                    }
            valid = usage is not None and all(
                isinstance(v, int) and v >= 0 for v in usage.values()
            )
            if valid:
                self.state["usage_reports"] += 1
                for key, value in usage.items():
                    self.totals[key] += value
            else:
                self.state["usage_complete"] = False
            self.journal.emit(
                "llm.finished",
                call_id=str(run_id),
                duration_s=self._duration(run_id),
                usage=usage if valid else None,
            )
            self.persist()

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self.journal.lock:
            self.state["llm_errors"] += 1
            self.state["usage_complete"] = False
            self.journal.emit(
                "llm.error",
                call_id=str(run_id),
                duration_s=self._duration(run_id),
                error_type=type(error).__name__,
                message=str(error),
            )
            self.persist()

    def _duration(self, run_id) -> float | None:
        start = self.started.pop(str(run_id), None)
        return round(monotonic() - start, 6) if start is not None else None

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self.journal.emit(
            "tool.started", call_id=str(run_id), tool=serialized.get("name")
        )

    def on_tool_end(self, output, *, run_id, **kwargs):
        # Full tool reports live in separate artifacts, not in the LLM journal.
        content = getattr(output, "content", output)
        summary = str(content)[:1000]
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    summary = {
                        k: parsed[k]
                        for k in ("run_id", "state", "error")
                        if k in parsed
                    }
            except ValueError:
                pass
        self.journal.emit(
            "tool.finished",
            call_id=str(run_id),
            output_chars=len(str(content)),
            result=summary,
        )

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.journal.emit(
            "tool.error",
            call_id=str(run_id),
            error_type=type(error).__name__,
            message=str(error),
        )
