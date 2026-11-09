#!/bin/sh
# Every suite, one after another; the exit code is non-zero if any of them failed.
status=0
run() {
    echo
    echo "=== $*"
    "$@" || status=1
}
run uv run --no-sync python -m pytest backend/agent/tests backend/openapi-parser/tests -q -p no:cacheprovider
run uv run --no-sync python -m pytest backend/testing_tools/tests -q -p no:cacheprovider
run uv run --no-sync mypy --config-file backend/testing_tools/pyproject.toml backend/testing_tools/src backend/testing_tools/tests
run uv run --no-sync ruff check backend/agent/src backend/agent/tests backend/agent/examples
run uv run --no-sync python -m pytest tests/acceptance -q -p no:cacheprovider -rs "$@"
exit $status
