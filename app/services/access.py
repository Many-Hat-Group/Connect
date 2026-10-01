"""Sharing, invites, transfers (playbook Phase 5)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session, SessionAccess, User, utcnow
from ..ws.hub import publish
from . import permissions, sessions, signal_tokens, viewers
from .events import Actor, emit


class AccessError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


async def get_active_user(db: AsyncSession, user_id) -> User:
    try:
        u = await db.get(User, uuid.UUID(str(user_id)))
    except ValueError:
        u = None
    if u is None or not u.is_active:
        raise AccessError(404, "That tech doesn't exist.")
    return u


async def share_all(db: AsyncSession, s: Session, user: User, ip) -> None:
    if s.visibility == "all_techs":
        return
    s.visibility = "all_techs"
    await emit(db, "session.shared_all", Actor.user(user), s.id, {}, ip=ip)
    await sessions.notify(db, s, {"type": "session.update"})
    for uid in await sessions.recipients(db, s):
        if uid not in (str(user.id),):
            await publish(f"ch:user:{uid}", {"type": "session.update", "session_id": str(s.id), "notify": {
                "title": f"{user.display_name} shared a session with all techs",
                "body": f"{s.customer_name or 'Customer'} ({s.customer_company or ''})",
                "actions": [{"label": "Open", "href": f"/portal/session/{s.id}", "primary": True}]}})


async def unshare(db: AsyncSession, s: Session, user: User, ip) -> None:
    if s.visibility != "all_techs":
        return
    before = await sessions.recipients(db, s)
    s.visibility = "private"
    await emit(db, "session.unshared", Actor.user(user), s.id, {}, ip=ip)
    after = await sessions.recipients(db, s)
    for uid in before - after:
        await drop_user(s, uid)
    await sessions.notify(db, s, {"type": "session.update"}, only=before)


async def invite(db: AsyncSession, s: Session, user: User, target_id, ip) -> SessionAccess:
    target = await get_active_user(db, target_id)
    if target.id == user.id:
        raise AccessError(400, "You already have access.")
    if await permissions.can_view(db, target, s) and s.visibility != "all_techs" and target.role != "admin":
        raise AccessError(400, f"{target.display_name} already has access.")
    existing = await db.scalar(select(SessionAccess).where(
        SessionAccess.session_id == s.id, SessionAccess.user_id == target.id, SessionAccess.status == "pending"))
    if existing:
        return existing
    a = SessionAccess(session_id=s.id, user_id=target.id, granted_by=user.id, kind="invite", status="pending")
    db.add(a)
    await db.flush()
    await emit(db, "session.invite_sent", Actor.user(user), s.id, {"to": str(target.id), "to_name": target.display_name},
               ip=ip)
    await publish(f"ch:user:{target.id}", {
        "type": "invite.received", "access_id": a.id, "session_id": str(s.id), "from": user.display_name,
        "customer": s.customer_name, "company": s.customer_company})
    return a


async def respond(db: AsyncSession, access_id: int, user: User, accept: bool, ip) -> SessionAccess:
    a = await db.get(SessionAccess, access_id)
    if a is None or a.user_id != user.id or a.status != "pending":
        raise AccessError(404, "That invite is no longer available.")
    s = await db.get(Session, a.session_id)
    if s is None or s.status == "ended":
        a.status = "revoked"
        await db.commit()
        raise AccessError(410, "That session has ended.")
    a.status = "accepted" if accept else "declined"
    a.responded_at = utcnow()
    await emit(db, "session.invite_accepted" if accept else "session.invite_declined", Actor.user(user), s.id,
               {"inviter": str(a.granted_by)}, ip=ip)
    if a.granted_by:
        await publish(f"ch:user:{a.granted_by}", {"type": "session.update", "session_id": str(s.id), "notify": {
            "title": f"{user.display_name} {'accepted' if accept else 'declined'} your invite",
            "body": f"{s.customer_name or 'Customer'} ({s.customer_company or ''})"}})
    await sessions.notify(db, s, {"type": "session.update"})
    return a


async def revoke(db: AsyncSession, access_id: int, user: User, ip) -> None:
    a = await db.get(SessionAccess, access_id)
    if a is None or a.status not in ("pending", "accepted"):
        raise AccessError(404, "That access entry doesn't exist.")
    s = await db.get(Session, a.session_id)
    if not (user.role == "admin" or s.owner_id == user.id or a.granted_by == user.id):
        raise AccessError(404, "That access entry doesn't exist.")
    a.status = "revoked"
    a.responded_at = utcnow()
    await emit(db, "session.invite_revoked", Actor.user(user), s.id, {"user": str(a.user_id)}, ip=ip)
    target = await db.get(User, a.user_id)
    if target and not await permissions.can_view(db, target, s):
        await drop_user(s, str(a.user_id))
    await sessions.notify(db, s, {"type": "session.update"})


async def drop_user(s: Session, user_id: str) -> None:
    """Someone lost access: close their views, revoke their signal tokens and
    tell their browser. (An established peer-to-peer media connection is cut
    by their own page closing the viewer; Kill cuts everyone.)"""
    for st in s.streams:
        await viewers.remove(str(st.id), user_id)
    await signal_tokens.revoke_user(str(s.id), user_id)
    await publish(f"ch:user:{user_id}", {"type": "access.revoked", "session_id": str(s.id)})
    from ..api.tech_api import push_customer_viewers
    await push_customer_viewers(None, s)


async def transfer(db: AsyncSession, s: Session, admin: User, target_id, ip) -> None:
    target = await get_active_user(db, target_id)
    if target.id == s.owner_id:
        return
    old_owner = s.owner_id
    before = await sessions.recipients(db, s)
    s.owner_id = target.id
    db.add(SessionAccess(session_id=s.id, user_id=target.id, granted_by=admin.id, kind="transfer",
                         status="accepted", responded_at=utcnow()))
    # The old owner keeps no implicit access.
    for a in (await db.scalars(select(SessionAccess).where(SessionAccess.session_id == s.id,
                                                           SessionAccess.user_id == old_owner,
                                                           SessionAccess.status == "accepted"))).all():
        a.status = "revoked"
    await emit(db, "session.transferred", Actor.user(admin), s.id,
               {"from": str(old_owner), "to": str(target.id), "to_name": target.display_name}, ip=ip)
    await db.refresh(s, ["owner"])
    after = await sessions.recipients(db, s)
    for uid in before - after:
        await drop_user(s, uid)
    await publish(f"ch:user:{target.id}", {"type": "session.update", "session_id": str(s.id), "notify": {
        "title": f"{admin.display_name} gave you a session",
        "body": f"{s.customer_name or 'Customer'} ({s.customer_company or ''})",
        "actions": [{"label": "Open", "href": f"/portal/session/{s.id}", "primary": True}], "sticky": True}})
    await sessions.notify(db, s, {"type": "session.update"}, only=before | after)


async def grant(db: AsyncSession, s: Session, admin: User, target_id, ip) -> None:
    target = await get_active_user(db, target_id)
    if await permissions.can_view(db, target, s):
        return
    db.add(SessionAccess(session_id=s.id, user_id=target.id, granted_by=admin.id, kind="admin",
                         status="accepted", responded_at=utcnow()))
    await emit(db, "session.access_granted", Actor.user(admin), s.id, {"to": str(target.id),
                                                                       "to_name": target.display_name}, ip=ip)
    await publish(f"ch:user:{target.id}", {"type": "session.update", "session_id": str(s.id), "notify": {
        "title": f"{admin.display_name} shared a session with you",
        "body": f"{s.customer_name or 'Customer'} ({s.customer_company or ''})",
        "actions": [{"label": "Open", "href": f"/portal/session/{s.id}", "primary": True}], "sticky": True}})
    await sessions.notify(db, s, {"type": "session.update"})


async def access_list(db: AsyncSession, s: Session) -> list[dict]:
    rows = (await db.execute(select(SessionAccess, User).join(User, User.id == SessionAccess.user_id)
                             .where(SessionAccess.session_id == s.id, SessionAccess.status.in_(("pending", "accepted")))
                             .order_by(SessionAccess.id))).all()
    return [{"id": a.id, "user_id": str(u.id), "name": u.display_name, "kind": a.kind, "status": a.status}
            for a, u in rows]
