"""
Request logging middleware (plain ASGI, so it runs in the same task as the endpoint).

For every request it:
  - assigns a request id (or reuses a safe X-Request-ID from the caller / load balancer)
  - returns it in the X-Request-ID response header, so a user can report it
  - logs one line at the end:  POST /auth/login -> 401 (231 ms)
  - catches crashes: logs the full traceback and returns a 500 that includes the request id
"""
import json
import logging
import re
import time
import uuid

from app.core.logging_config import request_id_var, user_id_var

logger = logging.getLogger("app.http")

# Health checks run every few seconds; only log them when they fail.
QUIET_PATHS = {"/health", "/ready"}
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        # Only trust well-formed ids (stops someone injecting fake lines into the logs)
        request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex[:12]
        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set("-")
        scope.setdefault("state", {})

        status_code = 500
        response_started = False
        start = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope["method"], scope["path"])
            if not response_started:
                body = json.dumps({"detail": "Internal server error", "request_id": request_id}).encode()
                await send_wrapper({
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({"type": "http.response.body", "body": body})
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            path = scope["path"]
            if not (path in QUIET_PATHS and status_code < 400):
                user_id = scope["state"].get("user_id", "-")
                user_id_var.set(user_id)
                logger.log(
                    logging.ERROR if status_code >= 500 else logging.INFO,
                    "%s %s -> %d (%.0f ms)", scope["method"], path, status_code, duration_ms,
                )
            request_id_var.reset(rid_token)
            user_id_var.reset(uid_token)
