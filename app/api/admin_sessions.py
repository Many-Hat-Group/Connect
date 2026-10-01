"""Admin: every session, live and history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Session, User
from ..web import render, require_admin

router = APIRouter(prefix="/admin/sessions")


@router.get("")
async def all_sessions(request: Request, status: str = "", q: str = "", days: int = 30,
                       admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    days = max(1, min(days, 400))
    stmt = select(Session).where(Session.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if status in ("pending", "waiting", "live", "ended"):
        stmt = stmt.where(Session.status == status)
    elif status == "active":
        stmt = stmt.where(Session.status != "ended")
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Session.customer_name.ilike(like), Session.customer_company.ilike(like),
                              Session.ticket_number.ilike(like)))
    rows = (await db.scalars(stmt.order_by(Session.created_at.desc()).limit(500))).unique().all()
    techs = (await db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))).all()
    counts = dict((await db.execute(select(Session.status, func.count()).group_by(Session.status))).all())
    return await render(request, db, "admin/sessions.html", {"rows": rows, "status": status, "q": q, "days": days,
                                                             "techs": techs, "counts": counts})
