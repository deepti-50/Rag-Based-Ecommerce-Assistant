import json
from typing import Optional
import redis.asyncio as aioredis
from app.core.config import settings

# Create Redis connection
redis_client = aioredis.from_url(
    settings.REDIS_URL,
    decode_responses=True,  # Return strings, not bytes
)

class RedisService:
    # ========================
    # SESSION MANAGEMENT
    # ========================
    @staticmethod
    async def create_session(user_id: str, ttl_seconds: int = 1800) -> None:
        """Create a session that expires in 30 minutes."""
        await redis_client.setex(f"session:{user_id}", ttl_seconds, "active")

    @staticmethod
    async def is_session_active(user_id: str) -> bool:
        """Check if user's session exists (not expired)."""
        return bool(await redis_client.exists(f"session:{user_id}"))

    @staticmethod
    async def extend_session(user_id: str, ttl_seconds: int = 1800) -> None:
        """Reset the 30-min timer on activity (sliding expiration)."""
        await redis_client.expire(f"session:{user_id}", ttl_seconds)

    @staticmethod
    async def delete_session(user_id: str) -> None:
        """Instantly invalidate a session (logout)."""
        await redis_client.delete(f"session:{user_id}")

    # ========================
    # CACHING & RATE LIMITING
    # ========================
    @staticmethod
    async def cache_set(key: str, data: dict, ttl_seconds: int = 3600) -> None:
        await redis_client.setex(key, ttl_seconds, json.dumps(data, default=str))

    @staticmethod
    async def cache_get(key: str) -> Optional[dict]:
        data = await redis_client.get(key)
        return json.loads(data) if data else None

    @staticmethod
    async def cache_delete(key: str) -> None:
        await redis_client.delete(key)

    @staticmethod
    async def check_rate_limit(user_id: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
        """Allow max 30 requests per 60 seconds per user."""
        key = f"ratelimit:{user_id}"
        current = await redis_client.incr(key)
        if current == 1:
            await redis_client.expire(key, window_seconds)
        return current <= max_requests

    # ========================
    # CONVERSATION STATE
    # ========================
    @staticmethod
    async def save_conversation_state(conversation_id: str, state: dict) -> None:
        """Save conversation state for 24 hours."""
        await redis_client.setex(f"conv:{conversation_id}", 86400, json.dumps(state, default=str))

    @staticmethod
    async def get_conversation_state(conversation_id: str) -> Optional[dict]:
        """Load conversation state."""
        data = await redis_client.get(f"conv:{conversation_id}")
        return json.loads(data) if data else None
