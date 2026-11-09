from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

import yaml


SUPPORTED_EXTENSIONS = {".json", ".yaml", ".yml"}
URL_SCHEMES = {"http", "https"}


class OpenAPILoadError(Exception):
    """Понятная ошибка загрузки OpenAPI-документа."""


class OpenAPILoader:
    """
    Загружает OpenAPI документ из:
      - локального пути (str | Path);
      - URL (http/https);
      - уже загруженного dict.

    Возвращает dict с документом.
    """

    def load(self, source: str | Path | dict[str, Any]) -> dict[str, Any]:
        if isinstance(source, dict):
            return source

        source_str = str(source)

        if self._is_url(source_str):
            return self._load_from_url(source_str)

        return self._load_from_file(Path(source_str))

    # ---------------------------------------------------------
    # URL
    # ---------------------------------------------------------

    @staticmethod
    def _is_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in URL_SCHEMES and bool(parsed.netloc)

    def _load_from_url(self, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=30) as response:  # noqa: S310
                raw = response.read()
        except Exception as exc:  # noqa: BLE001
            raise OpenAPILoadError(
                f"Не удалось загрузить OpenAPI по URL: {url}. Причина: {exc}"
            ) from exc

        text = raw.decode("utf-8")

        suffix = Path(urlparse(url).path).suffix.lower()

        return self._parse_text(text, suffix, source=url)

    # ---------------------------------------------------------
    # File
    # ---------------------------------------------------------

    def _load_from_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise OpenAPILoadError(
                f"Файл OpenAPI не найден: {path}"
            )

        if not path.is_file():
            raise OpenAPILoadError(
                f"Источник OpenAPI не является файлом: {path}"
            )

        suffix = path.suffix.lower()

        if suffix not in SUPPORTED_EXTENSIONS:
            raise OpenAPILoadError(
                f"Неподдерживаемый формат OpenAPI: {suffix}. "
                f"Поддерживаются: {sorted(SUPPORTED_EXTENSIONS)}"
            )

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OpenAPILoadError(
                f"Не удалось прочитать файл {path}: {exc}"
            ) from exc

        return self._parse_text(text, suffix, source=str(path))

    # ---------------------------------------------------------
    # Parse text
    # ---------------------------------------------------------

    def _parse_text(
        self,
        text: str,
        suffix: str,
        source: str,
    ) -> dict[str, Any]:
        try:
            if suffix == ".json" or (not suffix and text.lstrip().startswith("{")):
                document = json.loads(text)
            else:
                document = yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise OpenAPILoadError(
                f"Некорректный JSON/YAML в {source}: {exc}"
            ) from exc

        if not isinstance(document, dict):
            raise OpenAPILoadError(
                f"OpenAPI-документ должен быть JSON/YAML-объектом. Источник: {source}"
            )

        return document