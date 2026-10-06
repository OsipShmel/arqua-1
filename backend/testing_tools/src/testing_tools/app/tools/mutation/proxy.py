"""HTTP proxy between the agent's tests and the target that can break responses on purpose."""

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx2
import uvicorn

from testing_tools.api.schemas import Target
from testing_tools.app.tools.common import Observations, OperationIndex
from testing_tools.app.tools.mutation.mutants import MutantSpec

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "content-encoding",  # httpx hands us decoded bodies
    }
)

log = logging.getLogger(__name__)

Scope = dict[str, Any]
Receive = Any
Send = Any


class MutatingProxy:
    """ASGI app forwarding to the target; applies `active` to matching responses.

    Per test-suite run it records which operations were hit and whether the active
    mutant was actually applied (a mutant never applied cannot be killed).
    """

    def __init__(self, target: Target, index: OperationIndex) -> None:
        url = urlsplit(str(target.base_url))
        self.base_path = url.path.rstrip("/")
        self._origin = f"{url.scheme}://{url.netloc}"
        self._index = index
        self._headers = dict(target.headers)
        self._client = httpx2.AsyncClient(
            verify=target.tls_verify, timeout=target.request_timeout_s, follow_redirects=False
        )
        self.active: MutantSpec | None = None
        self.applied = 0
        self.observed = Observations()

    def reset(self, mutant: MutantSpec | None) -> None:
        self.active = mutant
        self.applied = 0
        self.observed = Observations()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break

        path: str = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        url = self._origin + path + (f"?{query}" if query else "")
        headers = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope["headers"]
            if k.decode("latin-1").lower() not in HOP_BY_HOP
        ]
        headers += list(self._headers.items())

        try:
            upstream = await self._client.request(
                scope["method"], url, headers=headers, content=bytes(body)
            )
        except httpx2.HTTPError as exc:
            log.warning("proxy: %s %s failed: %s", scope["method"], url, exc)
            await _respond(send, 502, [], f"upstream error: {exc}".encode())
            return

        status, content = upstream.status_code, upstream.content
        op_path = path[len(self.base_path) :] if path.startswith(self.base_path) else path
        op = self._index.match(scope["method"], op_path or "/")
        if op is not None:
            self.observed.add(op, status)
        if self.active is not None and self.active.targets(op, status):
            mutated = self.active.apply(status, content)
            if mutated is not None:
                status, content = mutated
                self.applied += 1

        out_headers = [
            (k, v) for k, v in upstream.headers.multi_items() if k.lower() not in HOP_BY_HOP
        ]
        await _respond(send, status, out_headers, content)


async def _respond(send: Send, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
    raw = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
    raw.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


class _QuietServer(uvicorn.Server):
    """An in-process server that leaves the host process' signal handlers alone."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


@asynccontextmanager
async def serve(proxy: MutatingProxy) -> AsyncIterator[str]:
    """Run the proxy on a free localhost port; yields the URL tests should use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(proxy, lifespan="off", log_level="warning", access_log=False)
    server = _QuietServer(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()  # surface the startup error
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}{proxy.base_path}"
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.shield(task)
        sock.close()
        await proxy.aclose()
