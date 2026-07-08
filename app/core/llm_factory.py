from langchain_openai import ChatOpenAI

from app.config import settings


def create_agent_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


def create_planning_llm() -> ChatOpenAI:
    """Higher temperature LLM for task decomposition."""
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0.3,
        max_tokens=settings.llm_max_tokens,
    )
