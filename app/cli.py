"""Admin CLI: python -m app.cli <command>."""
import asyncio
import sys

from sqlalchemy import func, select

from . import db
from .models import User


async def needs_setup() -> int:
    async with db.sessionmaker()() as s:
        n = await s.scalar(select(func.count()).select_from(User).where(User.role == "admin"))
    await db.dispose()
    return 0 if n == 0 else 1


COMMANDS = {"needs-setup": needs_setup}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("usage: python -m app.cli " + "|".join(COMMANDS))
        sys.exit(2)
    sys.exit(asyncio.run(COMMANDS[sys.argv[1]]()))
