"""Which chat model the agent uses. Ollama (local, free) is the only provider wired up so far."""
from langchain_core.language_models import BaseChatModel

from app.core.config import settings


def get_llm() -> BaseChatModel:
    if settings.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0,  # factual shop answers, not creative writing
            client_kwargs={"timeout": settings.LLM_TIMEOUT_SECONDS},
        )
    raise ValueError(f"LLM_PROVIDER={settings.LLM_PROVIDER!r} is not supported yet (use 'ollama')")
