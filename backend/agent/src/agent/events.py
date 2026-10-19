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


def redact(value):
    if isinstance(value, dict):
        return {
            k: "***" if str(k).lower() in SECRET_FIELDS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        key = os.getenv("DEEPCODE_API_KEY")
        return value.replace(key, "***") if key else value
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
