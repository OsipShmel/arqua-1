"""Bounded, static Python context. Never import or execute the supplied service."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

IGNORED = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "tests",
    "test",
    "site-packages",
    "runs",
}


def read_source_context(directory: Path, max_chars: int = 24_000) -> dict:
    root = directory.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Каталог исходников не найден: {root}")
    files, skipped = [], []
    examined = 0
    truncated = False
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            d
            for d in directories
            if d not in IGNORED
            and not d.startswith(".")
            and not (Path(parent) / d).is_symlink()
        )
        for name in sorted(names):
            if (
                not name.endswith(".py")
                or name.startswith(("test_", "."))
                or name.endswith("_test.py")
            ):
                continue
            examined += 1
            if examined > 500:
                truncated = True
                break
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or path.stat().st_size > 200_000:
                skipped.append({"path": relative, "reason": "symlink_or_size_limit"})
                continue
            try:
                content = path.read_bytes()
                text = content.decode("utf-8-sig")
                tree = ast.parse(text, filename=relative)
            except (UnicodeError, SyntaxError) as exc:
                skipped.append({"path": relative, "reason": type(exc).__name__})
                continue
            imports, units = [], []
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports.append(ast.unparse(node))
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    # A complete small unit or an explicitly marked fragment, never an entire module.
                    snippet = ast.get_source_segment(text, node) or ""
                    decorators = [ast.unparse(d) for d in node.decorator_list]
                    units.append(
                        {
                            "name": node.name,
                            "kind": type(node).__name__,
                            "line": node.lineno,
                            "decorators": decorators,
                            "code": snippet[:4000],
                            "truncated": len(snippet) > 4000,
                        }
                    )
            item = {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "imports": imports,
                "units": units,
            }
            if len(json.dumps([*files, item], ensure_ascii=False)) > max_chars:
                truncated = True
                skipped.append({"path": relative, "reason": "context_budget"})
                continue
            files.append(item)
        if examined > 500:
            break
    if not files:
        raise ValueError(
            "Не найдено доступных Python-файлов в лимите контекста исходников"
        )
    return {
        "root": str(root),
        "language": "python",
        "files": files,
        "skipped": skipped[:100],
        "truncated": truncated,
        "examined_files": min(examined, 500),
        "max_chars": max_chars,
    }
