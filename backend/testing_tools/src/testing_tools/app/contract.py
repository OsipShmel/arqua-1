import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeGuard

import httpx2
import yaml

from testing_tools.api.schemas import (
    ContractInfo,
    ContractSource,
    ErrorCode,
    HttpMethod,
    InlineContractSource,
    OperationFilter,
    OperationMatcher,
    OperationRef,
)
from testing_tools.app.errors import ApiError

FETCH_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ContractOperation:
    ref: OperationRef
    tags: tuple[str, ...]
    status_codes: tuple[str, ...]
    """Documented response keys as written in the contract: "200", "4XX", "default"."""


@dataclass(frozen=True)
class LoadedContract:
    info: ContractInfo
    operations: tuple[ContractOperation, ...]


async def load_contract(source: ContractSource, client: httpx2.AsyncClient) -> LoadedContract:
    if isinstance(source, InlineContractSource):
        return parse_contract(source.content)
    try:
        response = await client.get(
            str(source.url), headers=source.headers, timeout=FETCH_TIMEOUT_S
        )
    except httpx2.HTTPError as exc:
        raise ApiError(422, ErrorCode.CONTRACT_UNREACHABLE, f"{source.url}: {exc}") from exc
    if not response.is_success:
        raise ApiError(
            422, ErrorCode.CONTRACT_UNREACHABLE, f"{source.url}: HTTP {response.status_code}"
        )
    return parse_contract(response.text)


def parse_contract(text: str) -> LoadedContract:
    doc = _parse_document(text)
    openapi = doc.get("openapi")
    if not isinstance(openapi, str) or not openapi.startswith("3."):
        raise _invalid("not an OpenAPI 3.x document")
    info = doc.get("info")
    if not isinstance(info, dict) or "title" not in info or "version" not in info:
        raise _invalid("info.title and info.version are required")
    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        raise _invalid("paths must be an object")

    operations = tuple(_operations(paths))
    return LoadedContract(
        info=ContractInfo(
            title=str(info["title"]),
            version=str(info["version"]),
            openapi_version=openapi,
            operations_total=len(operations),
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        ),
        operations=operations,
    )


def select_operations(
    operations: Iterable[ContractOperation], operation_filter: OperationFilter | None
) -> list[ContractOperation]:
    include = operation_filter.include if operation_filter else None
    exclude = operation_filter.exclude if operation_filter else None
    return [
        op
        for op in operations
        if (not _has_criteria(include) or _matches(op, include))
        and not (_has_criteria(exclude) and _matches(op, exclude))
    ]


def _parse_document(text: str) -> dict[str, Any]:
    doc: Any
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise _invalid(f"neither JSON nor YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise _invalid("document root must be an object")
    return doc


def _operations(paths: dict[str, Any]) -> Iterable[ContractOperation]:
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in HttpMethod:
            op = item.get(method.lower())
            if not isinstance(op, dict):
                continue
            responses = op.get("responses")
            codes = tuple(str(c) for c in responses) if isinstance(responses, dict) else ()
            yield ContractOperation(
                ref=OperationRef(method=method, path=path, operation_id=op.get("operationId")),
                tags=tuple(str(t) for t in op.get("tags", [])),
                status_codes=codes,
            )


def _has_criteria(matcher: OperationMatcher | None) -> TypeGuard[OperationMatcher]:
    return matcher is not None and bool(
        matcher.path_regex or matcher.methods or matcher.operation_ids or matcher.tags
    )


def _matches(op: ContractOperation, matcher: OperationMatcher) -> bool:
    return all(
        (
            not matcher.path_regex or any(re.search(p, op.ref.path) for p in matcher.path_regex),
            not matcher.methods or op.ref.method in matcher.methods,
            not matcher.operation_ids or op.ref.operation_id in matcher.operation_ids,
            not matcher.tags or any(t in op.tags for t in matcher.tags),
        )
    )


def _invalid(detail: str) -> ApiError:
    return ApiError(422, ErrorCode.CONTRACT_INVALID, detail)
