import json
from typing import Optional
import redis.asyncio as aioredis
from app.core.config import settings

# Create Redis connection
redis_client = aioredis.from_url(
    settings.REDIS_URL,
    decode_responses=True,  # Return strings, not bytes
)

SESSION_TTL = settings.SESSION_IDLE_MINUTES * 60
REFRESH_TTL = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400


class RedisService:
    # ========================
    # SESSION MANAGEMENT
    # Keyed by session id (sid), not user id, so each login/device has its own
    # session and logging out on one device doesn't log out the others.
    # ========================
    @staticmethod
    async def create_session(sid: str, user_id: str) -> None:
        await redis_client.set(f"session:{sid}", user_id, ex=SESSION_TTL)

    @staticmethod
    async def touch_session(sid: str) -> bool:
        """Check-and-extend in ONE round trip: EXPIRE returns 0 if the key doesn't exist."""
        return bool(await redis_client.expire(f"session:{sid}", SESSION_TTL))

    @staticmethod
    async def delete_session(sid: str) -> None:
        """Logout: kill the session AND its refresh token."""
        await redis_client.delete(f"session:{sid}", f"refresh:{sid}")

    # ========================
    # REFRESH TOKEN ROTATION
    # Only the latest refresh token (by jti) for a session is valid.
    # ========================
    @staticmethod
    async def store_refresh_jti(sid: str, jti: str) -> None:
        await redis_client.set(f"refresh:{sid}", jti, ex=REFRESH_TTL)

    @staticmethod
    async def consume_refresh_jti(sid: str) -> Optional[str]:
        """Atomically read and delete, so a refresh token can be used exactly once."""
        return await redis_client.getdel(f"refresh:{sid}")

    # ========================
    # CACHING & RATE LIMITING
    # ========================
    @staticmethod
    async def cache_set(key: str, data: dict, ttl_seconds: int = 3600) -> None:
        await redis_client.set(key, json.dumps(data, default=str), ex=ttl_seconds)

    @staticmethod
    async def cache_get(key: str) -> Optional[dict]:
        data = await redis_client.get(key)
        return json.loads(data) if data else None

    @staticmethod
    async def cache_delete(key: str) -> None:
        await redis_client.delete(key)

    @staticmethod
    async def check_rate_limit(key: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
        """
        Fixed-window limiter. SET NX EX + INCR run in one MULTI transaction, so
        the key always has a TTL (the old INCR-then-EXPIRE could leave a key with
        no expiry if the process died in between -> user blocked forever).
        """
        k = f"ratelimit:{key}"
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.set(k, 0, ex=window_seconds, nx=True)
            pipe.incr(k)
            _, current = await pipe.execute()
        return current <= max_requests

    @staticmethod
    async def reset_rate_limit(key: str) -> None:
        await redis_client.delete(f"ratelimit:{key}")

    # ========================
    # CONVERSATION STATE
    # ========================
    @staticmethod
    async def save_conversation_state(conversation_id: str, state: dict) -> None:
        """Save conversation state for 24 hours."""
        await redis_client.set(f"conv:{conversation_id}", json.dumps(state, default=str), ex=86400)

    @staticmethod
    async def get_conversation_state(conversation_id: str) -> Optional[dict]:
        """Load conversation state."""
        data = await redis_client.get(f"conv:{conversation_id}")
        return json.loads(data) if data else None
