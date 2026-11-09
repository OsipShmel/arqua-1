"""Helpers shared by the real tool runners: operation lookup, coverage, HTTP snapshots."""

import copy
import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from testing_tools.api.schemas import (
    ContractCoverage,
    HttpMethod,
    HttpRequestSnapshot,
    HttpResponseSnapshot,
    OperationCoverage,
    OperationRef,
)
from testing_tools.app.contract import ContractOperation, LoadedContract

BODY_LIMIT = 64 * 1024
MASK = "***"
ALWAYS_MASKED = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


class OperationIndex:
    """Finds the contract operation behind a "METHOD /template" label or a concrete path."""

    def __init__(self, operations: Iterable[ContractOperation]) -> None:
        self._ops = list(operations)
        self._by_key = {(op.ref.method, op.ref.path): op for op in self._ops}
        # literal paths win over templated ones: /pets/mine before /pets/{petId}
        self._patterns = sorted(
            ((op, _template_regex(op.ref.path)) for op in self._ops),
            key=lambda item: item[0].ref.path.count("{"),
        )

    def by_label(self, label: str) -> ContractOperation | None:
        method, _, path = label.partition(" ")
        try:
            return self._by_key.get((HttpMethod(method.upper()), path.strip()))
        except ValueError:
            return None

    def match(self, method: str, path: str) -> ContractOperation | None:
        for op, pattern in self._patterns:
            if op.ref.method.value == method.upper() and pattern.fullmatch(path):
                return op
        return None


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}/]+\})", template.rstrip("/") or "/")
    regex = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(regex + "/?")


def label(op: ContractOperation | OperationRef) -> str:
    ref = op.ref if isinstance(op, ContractOperation) else op
    return f"{ref.method.value} {ref.path}"


def filtered_document(
    contract: LoadedContract, keep: Sequence[ContractOperation]
) -> dict[str, Any]:
    """A copy of the contract with only `keep` operations left in `paths`."""
    doc = copy.deepcopy(contract.document)
    wanted = {(op.ref.path, op.ref.method.value.lower()) for op in keep}
    paths: dict[str, Any] = doc.get("paths") or {}
    for path in list(paths):
        item = paths[path]
        if not isinstance(item, dict):
            continue
        for method in [m.value.lower() for m in HttpMethod]:
            if method in item and (path, method) not in wanted:
                del item[method]
        if not any(m.value.lower() in item for m in HttpMethod):
            del paths[path]
    return doc


def resolve(document: Mapping[str, Any], node: Any, depth: int = 0) -> Any:
    """Follow local `$ref`s (`#/components/...`); external refs are left as is."""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and depth < 32:
        ref: str = node["$ref"]
        if not ref.startswith("#/"):
            return node
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return node
            target = target[part]
        node = target
        depth += 1
    return node


# --------------------------------------------------------------------------- #
# Status codes and coverage
# --------------------------------------------------------------------------- #


def documented_key(op: ContractOperation, status: int) -> str | None:
    """The `responses` key that documents `status`: exact, then range ("4XX"), then default."""
    exact = str(status)
    if exact in op.status_codes:
        return exact
    for key in op.status_codes:
        if len(key) == 3 and key[1:].upper() == "XX" and key[0] == exact[0]:
            return key
    return "default" if "default" in op.status_codes else None


@dataclass
class Observations:
    """Requests and response codes seen per operation; feeds `ContractCoverage`."""

    requests: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, set[int]] = field(default_factory=dict)
    """Both keyed by operation label: "GET /pets/{petId}"."""

    def add(self, op: ContractOperation, status: int | None) -> None:
        key = label(op)
        self.requests[key] = self.requests.get(key, 0) + 1
        codes = self.statuses.setdefault(key, set())
        if status is not None:
            codes.add(status)


def build_coverage(
    operations: Sequence[ContractOperation], observed: Observations
) -> ContractCoverage:
    by_operation: list[OperationCoverage] = []
    documented = covered = undocumented = 0
    for op in operations:
        statuses = sorted(observed.statuses.get(label(op), set()))
        keys = {documented_key(op, status) for status in statuses}
        documented += len(op.status_codes)
        covered += len(keys & set(op.status_codes))
        undocumented += sum(documented_key(op, status) is None for status in statuses)
        requests = observed.requests.get(label(op), 0)
        by_operation.append(
            OperationCoverage(
                operation=op.ref,
                tested=requests > 0,
                documented_status_codes=list(op.status_codes),
                observed_status_codes=statuses,
                requests=requests,
            )
        )
    tested = sum(c.tested for c in by_operation)
    return ContractCoverage(
        operations_total=len(operations),
        operations_tested=tested,
        operations_ratio=tested / len(operations) if operations else 0.0,
        status_codes_documented=documented,
        status_codes_observed=covered,
        status_codes_ratio=covered / documented if documented else 0.0,
        undocumented_status_codes_observed=undocumented,
        by_operation=by_operation,
    )


# --------------------------------------------------------------------------- #
# HTTP snapshots
# --------------------------------------------------------------------------- #


class Masker:
    """Hides values of the caller's secret headers in everything we hand back."""

    def __init__(self, secret_headers: Iterable[str] = ()) -> None:
        self._names = ALWAYS_MASKED | {name.lower() for name in secret_headers}

    def headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        return {k: MASK if k.lower() in self._names else v for k, v in headers.items()}


def truncate(body: str | bytes | None) -> str | None:
    if body is None:
        return None
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if len(text) <= BODY_LIMIT:
        return text
    return text[:BODY_LIMIT] + f"... [truncated, {len(text)} chars]"


def request_snapshot(
    method: str, url: str, headers: Mapping[str, str], body: str | bytes | None, masker: Masker
) -> HttpRequestSnapshot | None:
    try:
        http_method = HttpMethod(method.upper())
    except ValueError:
        return None  # e.g. an exotic method from Schemathesis' unsupported_method check
    return HttpRequestSnapshot(
        method=http_method, url=url, headers=masker.headers(headers), body=truncate(body)
    )


def response_snapshot(
    status: int, headers: Mapping[str, str], body: str | bytes | None, elapsed_ms: float | None
) -> HttpResponseSnapshot:
    return HttpResponseSnapshot(
        status_code=status, headers=dict(headers), body=truncate(body), elapsed_ms=elapsed_ms
    )


def curl(request: HttpRequestSnapshot) -> str:
    parts = ["curl", "-X", request.method.value]
    for name, value in request.headers.items():
        parts += ["-H", f"{name}: {value}"]
    if request.body:
        parts += ["-d", request.body]
    parts.append(request.url)
    return shlex.join(parts)
