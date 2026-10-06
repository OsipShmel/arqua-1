"""Subprocesses that die with their tool: on timeout and on cancellation."""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

OUTPUT_LIMIT = 2 * 1024 * 1024
KILL_GRACE_S = 3.0

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    """None when the process was killed on timeout."""
    output: bytes
    """stdout and stderr interleaved; the tail if longer than OUTPUT_LIMIT."""

    @property
    def timed_out(self) -> bool:
        return self.returncode is None

    def tail(self, chars: int = 2000) -> str:
        return self.output.decode("utf-8", errors="replace")[-chars:]


async def run_process(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> ProcessResult:
    log.debug("exec %s (cwd=%s)", " ".join(args), cwd)
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # own process group, so kill() takes the children too
    )
    assert proc.stdout is not None
    chunks = bytearray()

    async def pump() -> None:
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(65536):
            chunks.extend(chunk)
            if len(chunks) > OUTPUT_LIMIT:
                del chunks[: len(chunks) - OUTPUT_LIMIT]

    reader = asyncio.create_task(pump())
    try:
        async with asyncio.timeout(timeout_s):
            await proc.wait()
            await reader
    except TimeoutError:
        await _kill(proc)
        reader.cancel()
        return ProcessResult(returncode=None, output=bytes(chunks))
    except BaseException:
        await asyncio.shield(_kill(proc))
        reader.cancel()
        raise
    return ProcessResult(returncode=proc.returncode, output=bytes(chunks))


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, None)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except TimeoutError:
            continue
