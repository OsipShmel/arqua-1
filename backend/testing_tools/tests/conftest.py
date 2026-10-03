from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from testing_tools.app.main import create_app
from testing_tools.app.settings import Settings


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
