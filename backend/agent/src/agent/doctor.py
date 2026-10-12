"""Offline configuration diagnostics; no provider requests or secret values."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def diagnose() -> dict:
    checks = []

    def record(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    record(
        "python",
        "ok" if sys.version_info >= (3, 11) else "error",
        f"Python {sys.version.split()[0]}; требуется 3.11+",
    )
    for package in (
        "agent",
        "arqa-openapi-parser",
        "langchain",
        "langchain-core",
        "langchain-openai",
        "httpx",
        "pydantic",
        "pyyaml",
        "python-dotenv",
    ):
        try:
            record(package, "ok", version(package))
        except PackageNotFoundError:
            record(
                package, "error", "Пакет отсутствует; выполните установку зависимостей"
            )

    try:
        from agent.model import read_model_settings

        read_model_settings()
        record("model_settings", "ok", "Обязательные настройки и значения корректны")
    except Exception as exc:
        # Error messages from settings describe variable names, never their values.
        record("model_settings", "error", str(exc) or type(exc).__name__)

    certificate = os.getenv("SSL_CERT_FILE")
    if certificate:
        record(
            "ssl_certificate",
            "ok" if Path(certificate).is_file() else "error",
            "SSL_CERT_FILE существует"
            if Path(certificate).is_file()
            else "Файл SSL_CERT_FILE не найден",
        )
    record("tools", "warning", "Tools демонстрационные; реальные тесты не выполняются")
    return {
        "status": "error" if any(c["status"] == "error" for c in checks) else "ok",
        "network_checked": False,
        "checks": checks,
    }
