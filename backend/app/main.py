"""
FastAPI entry point.
Run locally:  uvicorn app.main:app --reload   (from the backend/ directory)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.logging_config import setup_logging

setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

from app.core.database import engine  # noqa: E402
from app.core.middleware import RequestLoggingMiddleware  # noqa: E402
from app.core.redis_service import redis_client  # noqa: E402
from app.routers import auth, categories, products  # noqa: E402

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API starting (log level=%s, format=%s)", settings.LOG_LEVEL, settings.LOG_FORMAT)
    yield
    # Close pooled connections cleanly on shutdown (e.g. k8s SIGTERM during a rollout)
    logger.info("API shutting down")
    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(title="RAG E-commerce Assistant", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(products.router)


@app.get("/health", tags=["Health"])
async def health():
    """Liveness: the process is up. No dependency checks, so a DB blip doesn't restart pods."""
    return {"status": "ok"}


@app.get("/ready", tags=["Health"])
async def ready():
    """Readiness: can we serve traffic? Checks Postgres and Redis."""
    checks = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("Readiness check failed: database unavailable")
        checks["database"] = "unavailable"
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        logger.exception("Readiness check failed: redis unavailable")
        checks["redis"] = "unavailable"

    is_ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )
