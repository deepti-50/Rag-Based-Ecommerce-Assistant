import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.logging_config import mask_email, set_log_user
from app.core.security import (
    BCRYPT_MAX_BYTES, DUMMY_PASSWORD_HASH,
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
    TokenData,
)
from app.core.dependencies import get_current_user
from app.core.redis_service import RedisService
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger("app.auth")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, v: str) -> str:
        if len(v.encode()) > BCRYPT_MAX_BYTES:
            raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    phone: str | None
    is_admin: bool


async def _issue_tokens(user: User, sid: str) -> TokenResponse:
    """Create an access/refresh pair for a session and register it in Redis."""
    claims = {"sub": str(user.id), "email": user.email, "is_admin": user.is_admin, "sid": sid}
    access = create_access_token(claims)
    refresh = create_refresh_token(claims)
    await RedisService.create_session(sid, str(user.id))
    await RedisService.store_refresh_jti(sid, decode_token(refresh, "refresh").jti)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new user account."""
    result = await db.execute(select(User.id).where(User.email == body.email))
    if result.scalar_one_or_none():
        logger.info("registration rejected reason=email_taken email=%s", mask_email(body.email))
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        # bcrypt is CPU-bound (~250ms); run it off the event loop so other requests aren't frozen
        hashed_password=await run_in_threadpool(hash_password, body.password),
        full_name=body.full_name,
        phone=body.phone,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:  # two concurrent registrations with the same email
        await db.rollback()
        logger.info("registration rejected reason=email_taken_concurrent email=%s", mask_email(body.email))
        raise HTTPException(status_code=400, detail="Email already registered")

    set_log_user(request, str(user.id))
    logger.info("user registered email=%s", mask_email(body.email))
    return {"message": "User registered successfully", "user_id": str(user.id)}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate and return JWT tokens."""
    email = mask_email(body.email)
    rate_key = f"login:{body.email}"
    if not await RedisService.check_rate_limit(
        rate_key, settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_SECONDS
    ):
        logger.warning("login blocked reason=too_many_failed_attempts email=%s", email)
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Always run bcrypt, even for unknown emails, so timing doesn't leak which emails exist.
    password_ok = await run_in_threadpool(
        verify_password, body.password, user.hashed_password if user else DUMMY_PASSWORD_HASH
    )
    # The client always gets the same "Invalid credentials"; only the log says which one it was.
    if not user:
        logger.info("login failed reason=unknown_email email=%s", email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    set_log_user(request, str(user.id))
    if not password_ok:
        logger.info("login failed reason=wrong_password email=%s", email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        logger.info("login refused reason=account_deactivated email=%s", email)
        raise HTTPException(status_code=403, detail="Account deactivated")

    # Only failed attempts should count toward the limit
    await RedisService.reset_rate_limit(rate_key)
    sid = str(uuid.uuid4())
    logger.info("login ok session=%s", sid[:8])
    return await _issue_tokens(user, sid=sid)


@router.get("/me", response_model=UserResponse)
async def me(current_user: TokenData = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Return the logged-in user's profile."""
    user = await db.get(User, uuid.UUID(current_user.user_id))
    if not user or not user.is_active:
        logger.info("request rejected reason=user_missing_or_deactivated")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return UserResponse(
        id=str(user.id), email=user.email, full_name=user.full_name,
        phone=user.phone, is_admin=user.is_admin,
    )


@router.post("/logout")
async def logout(current_user: TokenData = Depends(get_current_user)):
    """Invalidate this session and its refresh token (instant logout)."""
    await RedisService.delete_session(current_user.sid)
    logger.info("logout session=%s", current_user.sid[:8])
    return {"message": "Logged out successfully"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new pair (the old refresh token stops working)."""
    try:
        token_data = decode_token(body.refresh_token, expected_type="refresh")
    except ValueError:
        logger.info("refresh failed reason=invalid_or_expired_refresh_token")
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    set_log_user(request, token_data.user_id)
    session = token_data.sid[:8]

    # One-time use: must match the latest jti stored for this session.
    # Missing -> logged out / expired. Mismatch -> an old token was replayed
    # (possible theft), so kill the whole session.
    current_jti = await RedisService.consume_refresh_jti(token_data.sid)
    if current_jti is None:
        logger.info("refresh failed reason=session_logged_out_or_expired session=%s", session)
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if current_jti != token_data.jti:
        await RedisService.delete_session(token_data.sid)
        logger.warning("refresh token REUSED, session revoked (possible token theft) session=%s", session)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Re-read the user so deactivation / admin changes take effect on refresh,
    # instead of being copied forward from the old token forever.
    user = await db.get(User, uuid.UUID(token_data.user_id))
    if not user or not user.is_active:
        await RedisService.delete_session(token_data.sid)
        logger.info("refresh failed reason=user_missing_or_deactivated session=%s", session)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    logger.info("tokens refreshed session=%s", session)
    return await _issue_tokens(user, sid=token_data.sid)
