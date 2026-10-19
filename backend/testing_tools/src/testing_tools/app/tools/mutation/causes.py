"""Why did a test fail? A heuristic from the exception type, as the design doc asks.

- infrastructure: the service or the stand didn't answer (connection, timeout, proxy 502);
- test_error: the test code itself is broken (imports, names, fixtures, a call outside
  the service under test);
- service_defect: the test reached the service and a check on its answer failed.
"""

from testing_tools.api.schemas import FailureCause, TestOutcome

NETWORK_GUARD_MARK = "testing_tools: network access"
INFRASTRUCTURE_TYPES = {
    "TransportError",  # httpx: ConnectError, ReadTimeout, RemoteProtocolError, ...
    "TimeoutException",  # httpx
    "ConnectionError",  # builtins, requests
    "Timeout",  # requests
    "TimeoutError",
    "NewConnectionError",  # urllib3
    "MaxRetryError",  # urllib3
}
SERVICE_DEFECT_TYPES = {
    "AssertionError",
    "KeyError",  # a field the test relies on is missing from the response
    "IndexError",
    "JSONDecodeError",  # the response is not the JSON the contract promises
}
PROXY_UPSTREAM_ERROR = "502"


def classify(
    outcome: TestOutcome,
    exc_types: list[str],
    message: str | None,
    when: str | None,
    upstream_errors: bool = False,
) -> FailureCause | None:
    if outcome not in (TestOutcome.FAILED, TestOutcome.ERROR):
        return None
    text = message or ""
    names = set(exc_types)
    if NETWORK_GUARD_MARK in text:
        return FailureCause.TEST_ERROR  # the test tried to bypass the service URL
    if names & INFRASTRUCTURE_TYPES:
        return FailureCause.INFRASTRUCTURE
    if upstream_errors and PROXY_UPSTREAM_ERROR in text:
        return FailureCause.INFRASTRUCTURE  # the proxy couldn't reach the service
    if when != "call":
        return FailureCause.TEST_ERROR  # setup/teardown: fixtures are test code
    if names & SERVICE_DEFECT_TYPES:
        return FailureCause.SERVICE_DEFECT
    return FailureCause.TEST_ERROR
