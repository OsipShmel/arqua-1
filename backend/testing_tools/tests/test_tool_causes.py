import pytest

from testing_tools.api.schemas import FailureCause
from testing_tools.api.schemas import TestOutcome as Outcome
from testing_tools.app.tools.mutation.causes import classify

SERVICE = FailureCause.SERVICE_DEFECT
TEST = FailureCause.TEST_ERROR
INFRA = FailureCause.INFRASTRUCTURE


@pytest.mark.parametrize(
    ("outcome", "exc_types", "message", "when", "expected"),
    [
        (Outcome.PASSED, [], None, None, None),
        (Outcome.SKIPPED, [], None, None, None),
        (Outcome.FAILED, ["AssertionError", "Exception"], "assert 500 == 400", "call", SERVICE),
        (Outcome.FAILED, ["KeyError", "LookupError"], "KeyError: 'status'", "call", SERVICE),
        (Outcome.FAILED, ["JSONDecodeError", "ValueError"], "Expecting value", "call", SERVICE),
        (Outcome.FAILED, ["ConnectError", "TransportError"], "refused", "call", INFRA),
        (Outcome.FAILED, ["ReadTimeout", "TimeoutException"], "timed out", "call", INFRA),
        (Outcome.FAILED, ["NameError"], "name 'x' is not defined", "call", TEST),
        (Outcome.FAILED, ["TypeError"], "unexpected keyword", "call", TEST),
        (Outcome.ERROR, ["FixtureLookupError"], "fixture 'db' not found", "setup", TEST),
        (Outcome.ERROR, ["ConnectError", "TransportError"], "refused", "setup", INFRA),
        (
            Outcome.FAILED,
            ["ConnectError", "TransportError"],
            "testing_tools: network access to 10.0.0.1:80 is blocked",
            "call",
            TEST,
        ),
    ],
)
def test_classify(
    outcome: Outcome,
    exc_types: list[str],
    message: str | None,
    when: str | None,
    expected: FailureCause | None,
) -> None:
    assert classify(outcome, exc_types, message, when) == expected


def test_proxy_502_is_infrastructure_only_when_upstream_failed() -> None:
    args = (Outcome.FAILED, ["AssertionError"], "assert 502 == 200", "call")

    assert classify(*args, upstream_errors=True) == INFRA
    assert classify(*args, upstream_errors=False) == SERVICE
