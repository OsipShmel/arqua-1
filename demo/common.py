"""What both demo services share: the contract file is the source of truth.

- GET /openapi.yaml serves the hand-written contract (not one generated from code),
  so the planted defects stay visible as differences between contract and behaviour.
- Invalid path/query parameters answer 400 {"detail": ...}, as the contracts document.
- A known path with an undocumented method answers 405 with a correct Allow header.
"""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def contract_app(contract: Path) -> FastAPI:
    text = contract.read_text()
    doc: dict[str, Any] = yaml.safe_load(text)
    app = FastAPI(
        title=doc["info"]["title"], openapi_url=None, docs_url=None, redoc_url=None
    )
    routes = [
        (_template(path), sorted(m.upper() for m in item if m in METHODS))
        for path, item in doc["paths"].items()
    ]

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi() -> Response:
        return Response(text, media_type="application/yaml")

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()))
        return JSONResponse({"detail": f"{where}: {first.get('msg', 'invalid')}"}, 400)

    @app.middleware("http")
    async def method_not_allowed(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        for pattern, allowed in routes:
            if pattern.fullmatch(request.url.path) and request.method not in allowed:
                return JSONResponse(
                    {"detail": "method not allowed"},
                    405,
                    headers={"Allow": ", ".join(allowed)},
                )
        return await call_next(request)

    return app


def _template(path: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}/]+\})", path)
    return re.compile("".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts))
