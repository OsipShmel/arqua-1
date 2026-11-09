"""Versioned agent result boundary and confined artifact materialization."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResultFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(
        description="Relative artifact path; confinement and Windows filename rules are checked before writing"
    )
    content: str = Field(max_length=14_000_000)
    kind: Literal["pytest", "junit", "log", "report", "other"] = "other"
    encoding: Literal["utf-8", "base64"] = "utf-8"

    def bytes(self) -> bytes:
        if self.encoding == "base64":
            return base64.b64decode(self.content, validate=True)
        return self.content.encode("utf-8")


class AgentResult(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "success"}},
                        "required": ["status"],
                    },
                    "then": {
                        "required": ["output"],
                        "properties": {"output": {"not": {"type": "null"}}},
                    },
                }
            ]
        },
    )
    result_version: Literal[1] = 1
    status: Literal["success", "error"]
    output: str | list | dict | None = None
    error: str | None = None
    error_type: str | None = None
    tool_mode: Literal["demo", "external", "real", "unknown"] = "unknown"
    metrics: dict | None = None
    execution: dict | None = None
    tool_runs: list[dict] = Field(default_factory=list)
    tool_artifacts: list[dict] = Field(default_factory=list)
    files: list[ResultFile] = Field(default_factory=list, max_length=200)
    runtime: dict | None = None

    @model_validator(mode="after")
    def require_output(self):
        if self.status == "success" and self.output is None:
            raise ValueError("В результате агента отсутствует output")
        return self


def safe_relative_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or ":" in name
        or any(
            p in {".", ".."}
            or not p
            or p.endswith((" ", "."))
            or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", p, re.I)
            for p in name.split("/")
        )
    ):
        raise ValueError("Недопустимый относительный путь артефакта")
    if any(ord(char) < 32 or char in '<>"|?*' for char in name):
        raise ValueError("Недопустимые символы в имени артефакта")
    return path


def save_result_files(directory: Path, files: list[ResultFile]) -> list[dict]:
    root = (directory / "agent_artifacts").resolve()
    if not root.is_relative_to(directory.resolve()):
        raise ValueError("Каталог артефактов выходит за каталог прогона")
    pending, total, names = [], 0, set()
    # Validate the whole batch before creating any file.
    for item in files:
        relative = safe_relative_path(item.path)
        canonical = str(relative).casefold()
        if canonical in names:
            raise ValueError("Дублирующийся путь артефакта")
        if any(
            canonical.startswith(p + "/") or p.startswith(canonical + "/")
            for p in names
        ):
            raise ValueError("Конфликт файла и каталога артефактов")
        names.add(canonical)
        if item.kind == "pytest" and not (
            relative.name.startswith("test_") and relative.suffix == ".py"
        ):
            raise ValueError("Pytest-артефакт должен называться test_*.py")
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Путь артефакта выходит за каталог прогона")
        data = item.bytes()
        total += len(data)
        if total > 10_000_000:
            raise ValueError("Превышен общий лимит артефактов: 10 MB")
        pending.append((item, path, data))
    saved = []
    for item, path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        saved.append(
            {
                "path": str(path),
                "kind": item.kind,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return saved
