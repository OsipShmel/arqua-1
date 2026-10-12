"""
Инициализация подключения к языковой модели через Deepcode API.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def _init_env() -> None:
    """Поиск корня проекта и загрузка .env."""
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        env_file = parent / ".env"
        if env_file.is_file():
            load_dotenv(env_file)
            break

    # A standalone CLI may be invoked outside the source checkout.
    # Process environment always wins over either .env file.
    load_dotenv(Path.cwd() / ".env", override=False)


def build_model(
    temperature: float | None = None,
    timeout: float | None = None,
    max_retries: int = 1,
) -> ChatOpenAI:
    """
    Фабрика создания настроенного инстанса ChatOpenAI.

    :raises RuntimeError: если отсутствуют обязательные переменные окружения.
    """
    _init_env()

    if temperature is None:
        temperature = float(os.getenv("DEEPCODE_TEMPERATURE", "0"))
    if timeout is None:
        timeout = float(os.getenv("DEEPCODE_TIMEOUT", "60"))
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError("DEEPCODE_TEMPERATURE должна быть конечным числом от 0 до 2")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DEEPCODE_TIMEOUT должен быть положительным конечным числом")

    api_key = os.getenv("DEEPCODE_API_KEY")
    base_url = os.getenv("DEEPCODE_BASE_URL")
    model_name = os.getenv("DEEPCODE_MODEL")

    missing = []
    if not api_key:
        missing.append("DEEPCODE_API_KEY")
    if not base_url:
        missing.append("DEEPCODE_BASE_URL")
    if not model_name:
        missing.append("DEEPCODE_MODEL")

    if missing:
        raise RuntimeError(
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}"
        )

    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
