"""
Logging setup.

Every log line carries the request id and user id of the request that produced it,
so `grep <request-id>` shows everything that happened during one request.

    text (default, for humans):
      2026-11-18 12:30:01 INFO    [req=3f2a9c1b7d4e user=-] app.auth: login failed reason=wrong_password email=a***@example.com
    json (LOG_FORMAT=json, for log tools like Loki/Grafana):
      {"ts": "...", "level": "INFO", "logger": "app.auth", "msg": "...", "request_id": "...", "user_id": "-"}

Never log passwords or tokens. Emails are masked with mask_email().
"""
import json
import logging
import sys
from contextvars import ContextVar

from fastapi import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

TEXT_FORMAT = "%(asctime)s %(levelname)-7s [req=%(request_id)s user=%(user_id)s] %(name)s: %(message)s"


class _ContextFilter(logging.Filter):
    """Copy the current request/user ids onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(_JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT, "%Y-%m-%d %H:%M:%S"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Send uvicorn's own messages (startup, errors) through the same format...
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # ...and turn off its access log: our request middleware logs a richer line instead.
    logging.getLogger("uvicorn.access").disabled = True


def set_log_user(request: Request, user_id: str) -> None:
    """Attach the user to this request's logs (and to the final request log line)."""
    request.state.user_id = user_id
    user_id_var.set(user_id)


def mask_email(email: str) -> str:
    """alice@example.com -> a***@example.com (enough to recognise, not to harvest)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
