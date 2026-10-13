"""pytest plugin loaded into the agent's test run (`-p arqa_network_guard`).

Lets the tests open TCP connections only to $ARQA_ALLOWED_PORTS on loopback, i.e. to
the mutating proxy in front of the service under test. Anything else fails with
PermissionError. This keeps generated tests from reaching other hosts by accident;
it is not a sandbox against hostile code (that is the container's job).
Runs inside the test environment, so it must only use the standard library.
"""

import os
import socket
from typing import Any

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_ALLOWED_PORTS = {
    int(p) for p in os.environ.get("ARQA_ALLOWED_PORTS", "").split(",") if p.strip()
}
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _check(sock: socket.socket, address: Any) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host, port = address[0], address[1]
    if host in _LOOPBACK and port in _ALLOWED_PORTS:
        return
    raise PermissionError(
        f"testing_tools: network access to {host}:{port} is blocked; "
        "tests may only call the service under test via its base URL"
    )


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check(self, address)
    _connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check(self, address)
    return _connect_ex(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign,assignment]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign,assignment]
