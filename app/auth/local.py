"""Local accounts: argon2id passwords plus mandatory TOTP."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from .base import AuthProvider, register
from .passwords import hash_password, needs_rehash, verify_password


class LocalAuthProvider(AuthProvider):
    name = "local"
    supports_password = True

    async def authenticate(self, db: AsyncSession, email: str, password: str) -> User | None:
        user = await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if user is None or user.auth_provider != self.name:
            verify_password(None, password)  # equalise timing
            return None
        if not verify_password(user.password_hash, password):
            return None
        if not user.is_active:
            return None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user


register(LocalAuthProvider())
