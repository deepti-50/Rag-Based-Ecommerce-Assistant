# RAG-Based E-commerce Assistant

FastAPI backend for an AI shopping assistant: Postgres (catalog, orders, chat history), Redis (sessions, rate limits), Chroma (vector search) and Ollama (LLM).

**Status:** foundation + authentication done. Catalog and RAG chat are next.

## Run locally

Prerequisites: Docker Desktop, Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), Ollama (native, for the RAG phase).

```bash
cd backend
cp .env.example .env          # then set SECRET_KEY (command is in the file)
```

**Option A – everything in Docker**

```bash
docker compose up --build     # from the repo root; runs migrations, then the API
```

**Option B – infra in Docker, API on your machine (hot reload)**

```bash
docker compose up -d postgres redis
cd backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs · Liveness: `/health` · Readiness: `/ready`

## Sample data and admin

```bash
docker compose exec api python -m app.scripts.seed                    # 8 categories, 200 products (only if empty)
docker compose exec api python -m app.scripts.seed --reset            # DEV ONLY: wipe catalog and reseed
docker compose exec api python -m app.scripts.make_admin you@example.com   # then log in again
```

(Running the API on your host? Use `python -m app.scripts.seed` from `backend/`.)

## API overview

| Endpoint | Who |
|---|---|
| `POST /auth/register`, `/auth/login`, `/auth/refresh` | anyone |
| `GET /auth/me`, `POST /auth/logout` | logged in |
| `GET /categories`, `GET /products`, `GET /products/{id}` | anyone |
| `POST / PATCH / DELETE` on `/categories` and `/products` | admin |

`GET /products` filters: `q` (text search), `category` (slug), `min_price`, `max_price`, `in_stock`, `sort` (`newest`, `price_asc`, `price_desc`, `rating`, `name`), `limit` (max 100), `offset`.
Deleting a product hides it (soft delete) because orders and reviews still point to it; `PATCH {"is_active": true}` brings it back.

## Tests

Needs Postgres + Redis running (`docker compose up -d postgres redis`). Tests use a separate `ecommerce_rag_test` database and Redis DB 15.

```bash
cd backend && pytest
```

## Troubleshooting with logs

```bash
docker compose logs -f api                 # live API logs (Ctrl+C to stop)
docker compose logs --since 10m api        # last 10 minutes
docker compose logs api | grep 3f2a9c1b7d4e   # everything from one request
docker compose logs api | grep "reason="   # every refused request, with why
```

Each line looks like:

```
2026-11-18 06:56:24 INFO    [req=8da029036475 user=0007a07e-...] app.auth: login failed reason=wrong_password email=d***@example.com
2026-11-18 06:56:24 INFO    [req=8da029036475 user=0007a07e-...] app.http: POST /auth/login -> 401 (208 ms)
```

- `req=`: request id. Every response also returns it in the `X-Request-ID` header, and crashes return it in the 500 body, so a user can report it.
- `user=`: who made the request (`-` if unknown).
- `reason=`: why a request was refused. Clients always get a generic message; the real reason is only in the logs.
- Times in Docker are UTC (IST = UTC + 5:30).
- Passwords and tokens are never logged. Emails are masked (`d***@example.com`).
- Successful `/health` and `/ready` checks are not logged (they run every 10 s); failures are.
- More detail: set `LOG_LEVEL=DEBUG` in `backend/.env`. For log tools (Loki/Grafana): `LOG_FORMAT=json`.

## Common tasks

| Task | Command (from `backend/`) |
|---|---|
| New migration after changing models | `alembic revision --autogenerate -m "describe change"` then review the file |
| Apply migrations | `alembic upgrade head` |
| Check models and DB are in sync | `alembic check` |
| Add a dependency | add it to `requirements.in`, then `uv pip compile requirements.in -o requirements.txt --universal --python-version 3.11` |

## Auth design (short)

- Short-lived access token (30 min) + refresh token (7 days), both JWT (HS256, PyJWT).
- Each login is a separate session (`sid`) in Redis with a 30-minute sliding idle timeout, so logout is instant and per device.
- Refresh tokens are single-use and rotate on every refresh. Replaying an old one revokes the whole session.
- Login is rate-limited on failed attempts (5 per 5 min per email).
