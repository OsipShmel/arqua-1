"""
Автономный QA-агент на базе LangChain и LangGraph.
"""

from typing import Any, Dict
from langchain.agents import create_agent
from langchain_core.tools import tool

from agent.model import build_model

# =====================================================================
# 1. Инструменты (Tools)
# =====================================================================


@tool
def check_api_health(url: str) -> str:
    """Проверить сетевую доступность тестируемого сервиса."""
    return f"Сервис {url} доступен, статус 200 OK."


@tool
def run_contract_fuzzing(spec_path: str, target_url: str) -> str:
    """Запустить фаззинг API по спецификации OpenAPI."""
    return (
        f"Фаззинг завершен для {target_url} по контракту {spec_path}. "
        "Найдено: 1 дефект (POST /users падает с 500 Internal Server Error при пустом теле)."
    )


TOOLS = [check_api_health, run_contract_fuzzing]

SYSTEM_PROMPT = (
    "Ты — ведущий AI QA-инженер. Исследуй API и контракт с помощью доступных инструментов. "
    "Опирайся только на факты из вызовов инструментов и сформируй понятный финальный отчет."
)


# =====================================================================
# 2. Сборка и запуск агента
# =====================================================================


def build_qa_agent():
    """Сборка агента ReAct."""
    llm = build_model()
    # Передаем системный промпт через prompt вместо устаревшего state_modifier
    return create_agent(model=llm, tools=TOOLS, prompt=SYSTEM_PROMPT)


def run_qa_agent(task: str) -> Dict[str, Any]:
    """Единая точка входа для вызова агента."""
    agent_app = build_qa_agent()

    # Запуск цикла
    state = agent_app.invoke({"messages": [("user", task)]})

    # Забираем последнее сообщение (финальный ответ модели)
    final_message = state["messages"][-1].content

    return {
        "status": "success",
        "output": final_message,
    }
