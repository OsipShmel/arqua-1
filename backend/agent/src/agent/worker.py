"""Private UTF-8 JSON protocol for the CLI's agent subprocess."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            from agent.agent import run_qa_agent

            result = run_qa_agent(request["task"], max_steps=request["max_steps"])
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
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
        serialized = json.dumps(result, ensure_ascii=False)
    print(serialized)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
