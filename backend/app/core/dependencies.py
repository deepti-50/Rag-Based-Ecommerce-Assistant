from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import decode_token, TokenData
from app.core.redis_service import RedisService

# Extracts "Bearer <token>" from the Authorization header
security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenData:
    """
    Dependency: Extract & validate the current user.
    """
    token = credentials.credentials

    # Step 1: Decode JWT
    try:
        token_data = decode_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    # Step 2: Check session in Redis
    if not await RedisService.is_session_active(token_data.user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )

    # Step 3: Extend session (sliding expiration)
    await RedisService.extend_session(token_data.user_id)

    return token_data

async def require_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency: Requires admin role."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
