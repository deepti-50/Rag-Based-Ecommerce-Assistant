"""
Promote an existing user to admin (there is deliberately no API for this).

    python -m app.scripts.make_admin someone@example.com
    docker compose exec api python -m app.scripts.make_admin someone@example.com

The user must log in again afterwards: is_admin is read into the token at login/refresh.
"""
import asyncio
import sys

from sqlalchemy import update

from app.core.database import async_session, engine
from app.models.user import User


async def main(email: str) -> int:
    async with async_session() as db:
        result = await db.execute(update(User).where(User.email == email.lower()).values(is_admin=True))
        await db.commit()
    await engine.dispose()
    if result.rowcount == 0:
        print(f"No user with email {email}. Register first via POST /auth/register.")
        return 1
    print(f"{email} is now an admin. Log in again to get an admin token.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
