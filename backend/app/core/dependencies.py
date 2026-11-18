import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.logging_config import set_log_user
from app.core.security import decode_token, TokenData
from app.core.redis_service import RedisService

logger = logging.getLogger("app.auth")

# Extracts "Bearer <token>" from the Authorization header
security = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TokenData:
    """Dependency: Extract & validate the current user."""
    # Step 1: Decode JWT. Must be an ACCESS token (a 7-day refresh token is rejected here).
    try:
        token_data = decode_token(credentials.credentials, expected_type="access")
    except ValueError:
        logger.info("request rejected reason=invalid_or_expired_access_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    set_log_user(request, token_data.user_id)

    # Step 2: Check + extend the session (sliding expiration) in one Redis call.
    if not await RedisService.touch_session(token_data.sid):
        logger.info("request rejected reason=session_expired_or_logged_out session=%s", token_data.sid[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )

    return token_data


async def require_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency: Requires admin role."""
    if not current_user.is_admin:
        logger.warning("admin access denied")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
