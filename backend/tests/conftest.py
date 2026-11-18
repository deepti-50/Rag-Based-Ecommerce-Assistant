"""
Test setup: real Postgres + Redis (from docker-compose), but a separate test
database and Redis DB 15, so tests never touch dev data.

    docker compose up -d postgres redis
    pytest
"""
import os

# Must be set BEFORE importing anything from app/ (settings are read at import time).
# Explicit env vars override values in .env.
TEST_DB_NAME = "ecommerce_rag_test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql+asyncpg://postgres:postgres@localhost:5432/{TEST_DB_NAME}"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["SECRET_KEY"] = "test-secret-key-0123456789-abcdefghijklmnop"

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.database import Base, engine  # noqa: E402
from app.core.redis_service import redis_client  # noqa: E402
import app.models as _models  # noqa: E402,F401  -- register all tables (aliased: don't shadow `app`)
from app.main import app  # noqa: E402


async def _ensure_test_database() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database):
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    """Fresh schema once per test run."""
    await _ensure_test_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    await redis_client.aclose()


@pytest.fixture(autouse=True)
async def _clean_state():
    """Every test starts with empty tables and an empty Redis DB."""
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await redis_client.flushdb()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------- helpers ----------

PASSWORD = "correct-horse-battery"


@pytest.fixture
def register(client):
    async def _register(email="alice@example.com", password=PASSWORD, full_name="Alice"):
        return await client.post(
            "/auth/register", json={"email": email, "password": password, "full_name": full_name}
        )
    return _register


@pytest.fixture
def login(client):
    async def _login(email="alice@example.com", password=PASSWORD):
        return await client.post("/auth/login", json={"email": email, "password": password})
    return _login


@pytest.fixture
async def tokens(register, login):
    """A registered + logged-in user's token pair."""
    assert (await register()).status_code == 201
    resp = await login()
    assert resp.status_code == 200
    return resp.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
