import asyncio

from sqlalchemy import update

from app.core.database import async_session
from app.models.user import User
from tests.conftest import PASSWORD, bearer


async def _deactivate(email: str) -> None:
    async with async_session() as db:
        await db.execute(update(User).where(User.email == email).values(is_active=False))
        await db.commit()


# ---------------- register ----------------

async def test_register_success(register):
    resp = await register()
    assert resp.status_code == 201
    assert resp.json()["user_id"]


async def test_register_duplicate_email_is_case_insensitive(register):
    assert (await register("bob@example.com")).status_code == 201
    resp = await register("BOB@Example.com")
    assert resp.status_code == 400


async def test_register_concurrent_duplicates_do_not_500(register):
    """Two simultaneous signups with the same email: one wins, the other gets 400 (not 500)."""
    r1, r2 = await asyncio.gather(register("race@example.com"), register("race@example.com"))
    assert sorted([r1.status_code, r2.status_code]) == [201, 400]


async def test_register_rejects_short_password(register):
    assert (await register(password="short")).status_code == 422


async def test_register_rejects_password_over_bcrypt_limit(register):
    assert (await register(password="a" * 73)).status_code == 422


# ---------------- login ----------------

async def test_login_returns_working_access_token(tokens, client):
    resp = await client.get("/auth/me", headers=bearer(tokens["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@example.com"


async def test_login_email_is_case_insensitive(register, login):
    await register()
    assert (await login(email="ALICE@example.com")).status_code == 200


async def test_login_wrong_password_and_unknown_email_look_identical(register, login):
    await register()
    wrong_pw = await login(password="wrong-password-123")
    unknown = await login(email="nobody@example.com")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


async def test_login_deactivated_account(register, login):
    await register()
    await _deactivate("alice@example.com")
    assert (await login()).status_code == 403


async def test_login_rate_limited_after_failed_attempts(register, login):
    await register()
    for _ in range(5):
        assert (await login(password="wrong-password-123")).status_code == 401
    # 6th attempt is blocked even with the right password
    assert (await login()).status_code == 429


async def test_successful_login_does_not_count_toward_limit(register, login):
    await register()
    for _ in range(4):
        await login(password="wrong-password-123")
    assert (await login()).status_code == 200  # resets the counter
    for _ in range(4):
        assert (await login(password="wrong-password-123")).status_code == 401
    assert (await login()).status_code == 200


# ---------------- token types ----------------

async def test_refresh_token_cannot_be_used_as_access_token(tokens, client):
    resp = await client.get("/auth/me", headers=bearer(tokens["refresh_token"]))
    assert resp.status_code == 401


async def test_access_token_cannot_be_used_to_refresh(tokens, client):
    resp = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


async def test_refresh_token_not_accepted_as_query_param(tokens, client):
    resp = await client.post("/auth/refresh", params={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 422


async def test_garbage_token_rejected(client):
    assert (await client.get("/auth/me", headers=bearer("not-a-jwt"))).status_code == 401


# ---------------- refresh rotation ----------------

async def test_refresh_returns_new_working_pair(tokens, client):
    resp = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new = resp.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert (await client.get("/auth/me", headers=bearer(new["access_token"]))).status_code == 200


async def test_refresh_token_replay_kills_session(tokens, client):
    """Reusing an already-rotated refresh token = likely theft -> whole session revoked."""
    first = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    new = first.json()

    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401

    # The legitimate new tokens are dead too
    assert (await client.get("/auth/me", headers=bearer(new["access_token"]))).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]})).status_code == 401


async def test_refresh_rejected_for_deactivated_user(tokens, client):
    await _deactivate("alice@example.com")
    resp = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


# ---------------- logout ----------------

async def test_logout_revokes_access_and_refresh(tokens, client):
    assert (await client.post("/auth/logout", headers=bearer(tokens["access_token"]))).status_code == 200
    assert (await client.get("/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401
    resp = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_logout_on_one_device_keeps_other_device_logged_in(register, login, client):
    await register()
    laptop = (await login()).json()
    phone = (await login()).json()

    await client.post("/auth/logout", headers=bearer(laptop["access_token"]))

    assert (await client.get("/auth/me", headers=bearer(laptop["access_token"]))).status_code == 401
    assert (await client.get("/auth/me", headers=bearer(phone["access_token"]))).status_code == 200


# ---------------- health ----------------

async def test_health_and_ready(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "redis": "ok"}
