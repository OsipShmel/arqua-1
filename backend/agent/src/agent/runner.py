"""Run only the agent in a cancellable child process; never dispatch tools here."""

from __future__ import annotations

import json
import subprocess
import sys


def invoke_agent(
    task: str, *, timeout: float, max_steps: int, context: dict | None = None
) -> dict:
    # stdin keeps the contract out of process command lines and avoids their size
    # limits on Windows. The worker reserves stdout for its JSON result.
    with subprocess.Popen(
        [sys.executable, "-m", "agent.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            request = {"task": task, "max_steps": max_steps}
            if context is not None:
                request["context"] = context
            output, _ = process.communicate(
                json.dumps(request, ensure_ascii=False),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise TimeoutError(
                f"Агент не завершился за {timeout:g} секунд (--agent-timeout)"
            ) from exc
        except BaseException:
            process.kill()
            process.communicate()
            raise
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Процесс агента завершился без корректного результата (код {process.returncode})"
        ) from exc
    if process.returncode != 0 and (
        not isinstance(result, dict) or result.get("status") != "error"
    ):
        raise RuntimeError(f"Процесс агента завершился с кодом {process.returncode}")
    return result
