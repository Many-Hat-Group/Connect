"""The one permission module used by every REST and websocket handler.

- can_view:  see the session and its streams.
- can_manage: end, share, invite, request remote control.
- can_admin: admin-only actions (kill any, transfer, settings).
Unauthorised lookups must surface as 404 (see load_session_for).
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session, SessionAccess, User


async def accepted_access(db: AsyncSession, session_id, user_id) -> SessionAccess | None:
    return await db.scalar(select(SessionAccess).where(
        SessionAccess.session_id == session_id, SessionAccess.user_id == user_id,
        SessionAccess.status == "accepted"))


async def can_view(db: AsyncSession, user: User, s: Session) -> bool:
    if not user.is_active:
        return False
    if user.role == "admin" or s.owner_id == user.id:
        return True
    if s.visibility == "all_techs":
        return True
    return await accepted_access(db, s.id, user.id) is not None


async def can_manage(db: AsyncSession, user: User, s: Session) -> bool:
    # Anyone who may view may also end, share and invite. Narrow here if needed.
    return await can_view(db, user, s)


def can_admin(user: User) -> bool:
    return user.is_active and user.role == "admin"


async def load_session_for(db: AsyncSession, user: User, session_id: str, need: str = "view") -> Session:
    """Fetch a session the user may access, or raise 404 (never 403)."""
    try:
        sid = uuid.UUID(str(session_id))
    except ValueError:
        raise HTTPException(404)
    s = await db.get(Session, sid)
    if s is None:
        raise HTTPException(404)
    ok = can_admin(user) if need == "admin" else await (can_manage if need == "manage" else can_view)(db, user, s)
    if not ok:
        raise HTTPException(404)
    return s


async def viewers_of(db: AsyncSession, s: Session) -> set[str]:
    """User ids that can view this session (for notification fan-out)."""
    ids = {str(s.owner_id)}
    rows = await db.scalars(select(SessionAccess.user_id).where(
        SessionAccess.session_id == s.id, SessionAccess.status == "accepted"))
    ids.update(str(r) for r in rows)
    return ids
