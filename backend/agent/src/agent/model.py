"""
Инициализация подключения к языковой модели через Deepcode API.
"""

from __future__ import annotations

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

    # Сбрасываем кастомные пути к сертификатам, если они выставлены в системе
    os.environ.pop("SSL_CERT_FILE", None)


def build_model(
    temperature: float = 0.0,
    timeout: float = 60.0,
    max_retries: int = 1,
) -> ChatOpenAI:
    """
    Фабрика создания настроенного инстанса ChatOpenAI.

    :raises RuntimeError: если отсутствуют обязательные переменные окружения.
    """
    _init_env()

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
        raise RuntimeError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")

    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
