import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from testing_tools.api.schemas import ErrorCode, FieldError, ProblemDetail

PROBLEM_JSON = "application/problem+json"

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: ErrorCode,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers


def problem_response(
    status: int,
    code: ErrorCode,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    problem = ProblemDetail(
        title=code.replace("_", " ").capitalize(),
        status=status,
        detail=detail,
        code=code,
        errors=errors or [],
    )
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=status,
        headers=headers,
        media_type=PROBLEM_JSON,
    )


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem_response(exc.status, exc.code, exc.detail, headers=exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        FieldError(loc=list(e["loc"]), msg=e["msg"], type=e["type"]) for e in exc.errors()
    ]
    return problem_response(
        422, ErrorCode.VALIDATION_ERROR, "Request does not match the schema", errors
    )


async def _internal_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error", exc_info=exc)
    return problem_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)
