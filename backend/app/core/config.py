"""
Module 2: Application Configuration
Reads all settings from .env file.
"""
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # "ignore" (not "allow") so a typo'd env var doesn't silently become a new attribute
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LOG_LEVEL: str = "INFO"                    # DEBUG for more detail while troubleshooting
    LOG_FORMAT: Literal["text", "json"] = "text"  # json for log tools (Loki/Grafana)

    DATABASE_URL: str ="postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce_rag"
    # Per-process pool. Total connections = (pool + overflow) x workers x replicas,
    # so keep this small and tune per deployment instead of hardcoding 20+10.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    REDIS_URL: str = "redis://localhost:6379/0"

    # Required, no default: the app must refuse to start rather than sign tokens
    # with a publicly known key. Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    SECRET_KEY: str = Field(min_length=32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SESSION_IDLE_MINUTES: int = 30  # sliding idle timeout for a login session

    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 300

    LLM_PROVIDER: str = "ollama"
    OLLAMA_MODEL: str = "llama3.1:8b"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    GOOGLE_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None

    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"  # chromadb's built-in ONNX embedder (free, runs locally)
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    CHROMA_COLLECTION: str = "products"

    # --- Assistant (app/agent) ---
    RAG_TOP_K: int = 5              # products returned per search
    AGENT_MAX_STEPS: int = 12       # LangGraph recursion_limit: caps agent<->tools loops
    CHAT_HISTORY_LIMIT: int = 20    # past messages sent to the LLM per turn
    LLM_TIMEOUT_SECONDS: int = 120


settings = Settings()
