"""SQLAlchemy models (playbook Section 5). All timestamps are timestamptz, UTC."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(10))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    recovery_codes: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # argon2 hashes
    auth_provider: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("role in ('admin','tech')", name="ck_users_role"),)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    share_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    visibility: Mapped[str] = mapped_column(String(10), default="private", server_default="private")
    vdo_room: Mapped[str] = mapped_column(String(64))
    vdo_password: Mapped[str] = mapped_column(String(64))
    customer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    customer_company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ticket_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    customer_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    os: Mapped[str | None] = mapped_column(String(40), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(40), nullable=True)
    browser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    device_class: Mapped[str | None] = mapped_column(String(10), nullable=True)
    audio_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True, index=True)
    end_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    owner: Mapped[User] = relationship(lazy="joined")
    streams: Mapped[list["SessionStream"]] = relationship(
        back_populates="session", lazy="selectin", order_by="SessionStream.started_at",
        cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status in ('pending','waiting','live','ended')", name="ck_sessions_status"),
        CheckConstraint("visibility in ('private','all_techs')", name="ck_sessions_visibility"),
    )


class JoinCode(Base):
    __tablename__ = "join_codes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(4), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    claimed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    claimed_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Unique among unclaimed, unrevoked codes. Expired codes are revoked by the
    # sweeper; issuance also checks expiry, so a stale unrevoked row only ever
    # blocks re-use of that exact code until the sweeper runs.
    __table_args__ = (
        Index("uq_join_codes_open", "code", unique=True,
              postgresql_where="claimed_at IS NULL AND revoked = false"),
    )


class SessionStream(Base):
    __tablename__ = "session_streams"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    vdo_stream_id: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(40))
    share_type: Mapped[str] = mapped_column(String(10))
    audio_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="starting")
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    session: Mapped[Session] = relationship(back_populates="streams")


class SessionAccess(Base):
    __tablename__ = "session_access"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    __table_args__ = (
        CheckConstraint("kind in ('invite','transfer','admin')", name="ck_access_kind"),
        CheckConstraint("status in ('pending','accepted','declined','revoked')", name="ck_access_status"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now(), index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    actor_type: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())


class RustdeskRelease(Base):
    __tablename__ = "rustdesk_releases"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    tag: Mapped[str] = mapped_column(String(60))
    published_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    assets: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status in ('active','previous','pending_approval','failed','archived')",
                        name="ck_rustdesk_status"),
    )
