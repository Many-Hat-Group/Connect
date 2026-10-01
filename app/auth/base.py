"""Authentication provider interface. Local accounts today; Entra ID SSO
can be added later as another provider (users.auth_provider, external_id)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User


class AuthProvider(ABC):
    name: str
    supports_password: bool = False
    #: Providers that do their own MFA (e.g. Entra Conditional Access) set False.
    requires_local_mfa: bool = True

    @abstractmethod
    async def authenticate(self, db: AsyncSession, email: str, password: str) -> User | None:
        """Return the active user if the credentials are valid."""


_providers: dict[str, AuthProvider] = {}


def register(p: AuthProvider) -> None:
    _providers[p.name] = p


def get(name: str) -> AuthProvider:
    return _providers[name]


def password_providers() -> list[AuthProvider]:
    return [p for p in _providers.values() if p.supports_password]
