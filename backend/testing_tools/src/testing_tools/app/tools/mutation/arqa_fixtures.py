"""pytest plugin loaded into the agent's test run (`-p arqa_fixtures`).

Trusted fixtures of the execution environment, so generated tests don't have to know
how the service is reached:

    def test_list_pets(http_client):
        assert http_client.get("/pets").status_code == 200

- `base_url` — URL of the service under test (through the mutation proxy);
- `http_client` — `httpx.Client` bound to `base_url`.

A suite's own conftest.py fixtures with the same names take precedence.
"""

import os
from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ["ARQA_TARGET_URL"]


@pytest.fixture
def http_client(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
