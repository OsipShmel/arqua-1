"""Request checks that the Pydantic schema can't express."""

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi.exceptions import RequestValidationError

from testing_tools.api.schemas import (
    ErrorCode,
    InlineTestSuite,
    MutationConfig,
    RunCreateRequest,
    SchemathesisConfig,
    ToolName,
)
from testing_tools.app.errors import ApiError


def check_path_regexes(request: RunCreateRequest) -> None:
    errors: list[dict[str, Any]] = []
    for i, tool in enumerate(request.tools):
        if not isinstance(tool, SchemathesisConfig | MutationConfig) or tool.operations is None:
            continue
        for part in ("include", "exclude"):
            matcher = getattr(tool.operations, part)
            for j, pattern in enumerate(matcher.path_regex if matcher else []):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(
                        {
                            "loc": ("body", "tools", i, "operations", part, "path_regex", j),
                            "msg": f"Invalid regular expression: {exc}",
                            "type": "regex_invalid",
                        }
                    )
    if errors:
        raise RequestValidationError(errors)


def check_tools_available(request: RunCreateRequest, unavailable: frozenset[ToolName]) -> None:
    missing = [tool.tool.value for tool in request.tools if tool.tool in unavailable]
    if missing:
        raise ApiError(503, ErrorCode.TOOL_UNAVAILABLE, f"unavailable tools: {', '.join(missing)}")


def check_test_suites(request: RunCreateRequest) -> None:
    for tool in request.tools:
        if not isinstance(tool, MutationConfig) or not isinstance(tool.tests, InlineTestSuite):
            continue
        bad = [path for path in tool.tests.files if not _is_safe_relative(path)]
        if bad:
            raise ApiError(
                422,
                ErrorCode.TEST_SUITE_INVALID,
                f"test file paths must be relative and stay inside the suite: {bad}",
            )


def _is_safe_relative(path: str) -> bool:
    posix = PurePosixPath(path)
    return (
        bool(path.strip())
        and not posix.is_absolute()
        and not PureWindowsPath(path).anchor
        and ".." not in posix.parts
        and ".." not in PureWindowsPath(path).parts
    )
