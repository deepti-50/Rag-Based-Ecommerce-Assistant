import logging

from app.main import app
from tests.conftest import PASSWORD, bearer


@app.get("/__test_crash", include_in_schema=False)
async def _crash():
    raise RuntimeError("boom")


async def test_every_response_has_request_id(client):
    resp = await client.get("/health")
    assert len(resp.headers["x-request-id"]) == 12


async def test_safe_incoming_request_id_is_reused(client):
    resp = await client.get("/health", headers={"X-Request-ID": "lb-abc123"})
    assert resp.headers["x-request-id"] == "lb-abc123"


async def test_unsafe_incoming_request_id_is_replaced(client):
    resp = await client.get("/health", headers={"X-Request-ID": "x\ninjected log line"})
    assert resp.headers["x-request-id"] != "x\ninjected log line"


async def test_failed_login_logs_reason_and_masked_email_never_password(register, login, caplog):
    await register()
    caplog.set_level(logging.INFO)
    await login(password="wrong-password-123")

    text = caplog.text
    assert "login failed reason=wrong_password email=a***@example.com" in text
    assert "alice@example.com" not in text
    assert "wrong-password-123" not in text
    assert "POST /auth/login -> 401" in text


async def test_successful_request_log_includes_user_and_no_tokens(tokens, client, caplog):
    caplog.set_level(logging.INFO)
    await client.get("/auth/me", headers=bearer(tokens["access_token"]))

    request_line = next(r for r in caplog.records if r.name == "app.http")
    assert request_line.getMessage().startswith("GET /auth/me -> 200")
    assert request_line.user_id != "-"
    assert tokens["access_token"] not in caplog.text
    assert PASSWORD not in caplog.text


async def test_health_checks_are_not_logged_when_ok(client, caplog):
    caplog.set_level(logging.INFO)
    await client.get("/ready")
    assert not [r for r in caplog.records if r.name == "app.http"]


async def test_crash_returns_500_with_request_id_and_logs_traceback(client, caplog):
    caplog.set_level(logging.INFO)
    resp = await client.get("/__test_crash")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error", "request_id": resp.headers["x-request-id"]}
    assert "RuntimeError: boom" in caplog.text
