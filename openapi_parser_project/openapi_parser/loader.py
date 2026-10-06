from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_EXTENSIONS = {".json", ".yaml", ".yml"}


class OpenAPILoader:
    """
    Загружает OpenAPI документ из JSON/YAML файла
    или принимает уже загруженный dict.
    """

    def load(self, source: str | Path | dict[str, Any]) -> dict[str, Any]:
        if isinstance(source, dict):
            return source

        path = Path(source)

        if not path.exists():
            raise FileNotFoundError(
                f"OpenAPI file does not exist: {path}"
            )

        if not path.is_file():
            raise ValueError(
                f"OpenAPI source is not a file: {path}"
            )

        suffix = path.suffix.lower()

        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported OpenAPI file format: {suffix}. "
                f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )

        text = path.read_text(encoding="utf-8")

        if suffix == ".json":
            document = json.loads(text)
        else:
            document = yaml.safe_load(text)

        if not isinstance(document, dict):
            raise ValueError(
                "OpenAPI document must be a JSON/YAML object"
            )

        return document