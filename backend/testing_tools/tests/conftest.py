import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from buggy_petstore import create_app as create_petstore
from testing_tools.app.contract import LoadedContract, parse_contract
from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings

CONTRACT_PATH = Path(__file__).with_name("petstore_openapi.yaml")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's TT_* variables and .env out of the tests."""
    for name in os.environ:
        if name.startswith("TT_"):
            monkeypatch.delenv(name)
    # Gateway tests run on stub tools; real tool runners have their own tests.
    monkeypatch.setenv("TT_TOOL_MODE", "stub")
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings() -> Settings:
    return Settings(tool_delay_s=0.0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def petstore_url() -> Iterator[str]:
    """The buggy petstore served on a free localhost port for the whole session."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_petstore(), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "petstore did not start"
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def contract() -> LoadedContract:
    """The buggy petstore's contract (petstore_openapi.yaml), not `petstore.PETSTORE`."""
    return parse_contract(CONTRACT_PATH.read_text())
