"""The single event pipeline: every audit-worthy action goes through emit().

emit() writes the audit row and publishes the event on Redis channel
"events". That channel is the hook point for future webhooks (Teams etc.).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent, utcnow
from ..rds import redis

log = logging.getLogger("connect.events")

EVENT_TYPES = {
    "auth.login", "auth.login_failed", "auth.mfa_failed", "auth.logout", "auth.locked_out",
    "auth.mfa_enrolled", "auth.password_changed", "auth.recovery_code_used",
    "user.created", "user.updated", "user.mfa_reset", "user.password_reset",
    "setup.completed",
    "code.issued", "code.claimed", "code.failed_attempt", "code.expired", "code.ip_locked",
    "code.bruteforce_alert", "code.cancelled",
    "session.details_submitted", "session.stream_started", "session.stream_stopped",
    "session.viewed", "session.view_closed", "session.audio_unmuted", "session.quality_changed",
    "session.shared_all", "session.invite_sent", "session.invite_accepted", "session.invite_declined",
    "session.invite_revoked", "session.transferred", "session.admin_joined", "session.access_granted",
    "session.unshared",
    "session.rustdesk_prompted", "session.rustdesk_downloaded", "session.rustdesk_declined",
    "session.ended", "session.ip_blocked",
    "settings.changed", "rustdesk.synced", "rustdesk.sync_failed", "rustdesk.pinned",
    "rustdesk.approved", "rustdesk.rolled_back",
    "audit.exported",
}


@dataclass(frozen=True)
class Actor:
    type: str  # tech | admin | customer | system
    id: str | None = None
    name: str | None = None

    @classmethod
    def user(cls, user) -> "Actor":
        return cls(type="admin" if user.role == "admin" else "tech", id=str(user.id), name=user.display_name)

    @classmethod
    def customer(cls, session_id) -> "Actor":
        return cls(type="customer", id=str(session_id))


SYSTEM = Actor(type="system")


async def emit(db: AsyncSession, event_type: str, actor: Actor, session_id=None, data: dict | None = None,
               ip: str | None = None, commit: bool = True) -> AuditEvent:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type}")
    data = dict(data or {})
    if actor.name and "actor_name" not in data:
        data["actor_name"] = actor.name
    ev = AuditEvent(ts=utcnow(), event_type=event_type, actor_type=actor.type, actor_id=actor.id,
                    session_id=uuid.UUID(str(session_id)) if session_id else None, ip=ip, data=data)
    db.add(ev)
    if commit:
        await db.commit()
    else:
        await db.flush()
    payload = {"id": ev.id, "ts": ev.ts.isoformat(), "type": event_type, "actor_type": actor.type,
               "actor_id": actor.id, "session_id": str(session_id) if session_id else None, "data": data}
    try:
        await redis().publish("events", json.dumps(payload, default=str))
    except Exception:  # the audit row is the record; fan-out is best effort
        log.exception("failed to publish event %s", event_type)
    return ev
