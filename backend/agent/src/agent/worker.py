"""Private UTF-8 JSON protocol for the CLI's agent subprocess."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from agent.events import Journal, redact


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    telemetry, gateway = None, None
    journal = Journal(None, "direct")
    try:
        request = json.load(sys.stdin)
        context = request.get("context", {})
        journal = Journal(
            Path(context["events_path"]) if context.get("events_path") else None,
            context.get("run_id", "direct"),
        )
        journal.emit("agent.started")
        with redirect_stdout(sys.stderr):
            from agent.agent import run_qa_agent
            from agent.telemetry import RunTelemetry

            telemetry = RunTelemetry(
                journal,
                Path(context["runtime_path"]) if context.get("runtime_path") else None,
            )
            if context.get("gateway"):
                from agent.gateway import ToolsGateway

                gateway = ToolsGateway(context["gateway"], journal)

            result = run_qa_agent(
                request["task"],
                max_steps=request["max_steps"],
                telemetry=telemetry,
                gateway=gateway,
            )
        journal.emit("agent.finished", status=result.get("status"))
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        key = os.getenv("DEEPCODE_API_KEY")
        if key:
            message = message.replace(key, "***")
        result = {
            "status": "error",
            "error": message,
            "error_type": type(exc).__name__,
            "tool_mode": "demo",
        }
        journal.emit("agent.error", error_type=type(exc).__name__, message=message)
    if telemetry:
        telemetry.persist()
        result["runtime"] = telemetry.state
    if gateway:
        result.update(gateway.outcome())
    serialized = json.dumps(redact(result), ensure_ascii=False, allow_nan=False)
    print(serialized)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
