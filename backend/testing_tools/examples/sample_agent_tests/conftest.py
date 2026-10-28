import os

import httpx
import pytest


@pytest.fixture
def api():
    with httpx.Client(base_url=os.environ["ARQA_BASE_URL"], timeout=10) as client:
        yield client
