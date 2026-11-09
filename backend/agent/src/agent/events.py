"""Append-only run journal with structured redaction and atomic JSON snapshots."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

SECRET_FIELDS = {
    "api_key",
    "apikey",
    "password",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "access_token",
}


# Local models take dummy keys such as "local" or "ollama"; cutting those out of every
# string would corrupt model names and URLs ("localhost" -> "***host"). Such keys are
# short words ("ollama", "lm-studio", "not-needed"); real provider keys are far longer.
MIN_SECRET_LENGTH = 12


def mask_key(text: str) -> str:
    key = os.getenv("DEEPCODE_API_KEY")
    if key and len(key) >= MIN_SECRET_LENGTH:
        return text.replace(key, "***")
    return text


def redact(value):
    if isinstance(value, dict):
        return {
            k: "***" if str(k).lower() in SECRET_FIELDS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return mask_key(value)
    return value


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


class Journal:
    def __init__(self, path: Path | None, run_id: str):
        self.path, self.run_id = path, run_id
        self.lock = RLock()

    def emit(self, event: str, **data) -> None:
        if self.path is None:
            return
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **redact(data),
        }
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
