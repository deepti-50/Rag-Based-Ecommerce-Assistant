from datetime import datetime, timedelta, timezone
from typing import Literal
import uuid

import bcrypt
import jwt  # PyJWT (python-jose is unmaintained: CVE-2024-33663 / CVE-2024-33664)
from pydantic import BaseModel

from app.core.config import settings

TokenType = Literal["access", "refresh"]

# bcrypt is used directly: passlib 1.7.4 is unmaintained and crashes on import
# with bcrypt>=4.1/5.0. bcrypt only looks at the first 72 bytes, so the API
# layer rejects longer passwords.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    except ValueError:  # malformed hash or >72-byte input
        return False


# Used when the email doesn't exist, so login takes the same time either way
# (otherwise response time reveals which emails are registered).
DUMMY_PASSWORD_HASH = hash_password(uuid.uuid4().hex)


class TokenData(BaseModel):
    """Data extracted from a JWT token."""
    user_id: str
    email: str
    is_admin: bool = False
    sid: str          # login-session id (one per login/device)
    jti: str
    type: TokenType


def _create_token(claims: dict, token_type: TokenType, expires_in: timedelta) -> str:
    payload = {
        **claims,
        "exp": datetime.now(timezone.utc) + expires_in,
        "jti": str(uuid.uuid4()),
        "type": token_type,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(claims: dict) -> str:
    return _create_token(claims, "access", timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(claims: dict) -> str:
    return _create_token(claims, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str, expected_type: TokenType) -> TokenData:
    """Decode a JWT and reject it unless it is the expected kind (access vs refresh)."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],  # pinned list: blocks alg=none / alg-confusion
            options={"require": ["exp", "sub", "jti", "type", "sid"]},
        )
        data = TokenData(
            user_id=payload["sub"],
            email=payload["email"],
            is_admin=payload.get("is_admin", False),
            sid=payload["sid"],
            jti=payload["jti"],
            type=payload["type"],
        )
    except (jwt.InvalidTokenError, KeyError, ValueError):  # covers expired, bad signature, malformed
        raise ValueError("Invalid or expired token")
    if data.type != expected_type:
        raise ValueError("Invalid or expired token")
    return data
