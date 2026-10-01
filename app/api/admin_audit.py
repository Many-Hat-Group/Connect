"""Admin audit log: filters and CSV export (opens cleanly in Excel)."""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import AuditEvent, Session, User
from ..services.events import EVENT_TYPES, Actor, emit
from ..web import client_ip, render, require_admin

router = APIRouter(prefix="/admin/audit")
PAGE = 100


def _date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def build_query(params):
    q = select(AuditEvent, Session.customer_company, Session.customer_name).outerjoin(
        Session, Session.id == AuditEvent.session_id)
    f, t = _date(params.get("from")), _date(params.get("to"))
    tz_offset = int(params.get("tz") or 0)  # browser offset in minutes, so dates mean local days
    shift = timedelta(minutes=tz_offset)
    if f:
        q = q.where(AuditEvent.ts >= datetime.combine(f, time.min, timezone.utc) + shift)
    if t:
        q = q.where(AuditEvent.ts < datetime.combine(t + timedelta(days=1), time.min, timezone.utc) + shift)
    if params.get("tech"):
        q = q.where(AuditEvent.actor_id == params["tech"])
    if params.get("type") in EVENT_TYPES:
        q = q.where(AuditEvent.event_type == params["type"])
    elif params.get("type", "").endswith(".*"):
        q = q.where(AuditEvent.event_type.startswith(params["type"][:-1]))
    if params.get("session"):
        try:
            q = q.where(AuditEvent.session_id == uuid.UUID(params["session"]))
        except ValueError:
            q = q.where(False)
    if params.get("company"):
        q = q.where(Session.customer_company.ilike(f"%{params['company'].strip()}%"))
    return q.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())


def _params(request: Request) -> dict:
    keys = ("from", "to", "tech", "type", "session", "company", "tz")
    return {k: request.query_params.get(k, "").strip() for k in keys}


@router.get("")
async def audit_page(request: Request, page: int = 1, admin: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    params = _params(request)
    page = max(1, page)
    rows = (await db.execute(build_query(params).offset((page - 1) * PAGE).limit(PAGE + 1))).all()
    users = (await db.scalars(select(User).order_by(User.display_name))).all()
    names = {str(u.id): u.display_name for u in users}
    return await render(request, db, "admin/audit.html", {
        "rows": rows[:PAGE], "more": len(rows) > PAGE, "page": page, "params": params, "users": users,
        "names": names, "types": sorted(EVENT_TYPES)})


def _safe(v) -> str:
    """Stop spreadsheet formula injection (cells starting = + - @ or tab/CR)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get(".csv")
async def audit_csv(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    params = _params(request)
    users = {str(u.id): u.display_name for u in (await db.scalars(select(User))).all()}
    rows = (await db.execute(build_query(params).limit(200_000))).all()
    await emit(db, "audit.exported", Actor.user(admin), data={"filters": params, "rows": len(rows)},
               ip=client_ip(request))

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        buf.write("﻿")  # UTF-8 BOM so Excel reads accents and non-Latin names correctly
        w.writerow(["Time (UTC)", "Event", "Actor type", "Actor", "Session", "Customer", "Company", "IP", "Details"])
        yield buf.getvalue()
        for ev, company, cname in rows:
            buf.seek(0); buf.truncate()
            w.writerow([ev.ts.strftime("%Y-%m-%d %H:%M:%S"), ev.event_type, ev.actor_type,
                        _safe(users.get(ev.actor_id or "", (ev.data or {}).get("actor_name") or ev.actor_id or "")),
                        str(ev.session_id or ""), _safe(cname), _safe(company), str(ev.ip or ""),
                        _safe(json.dumps(ev.data or {}, ensure_ascii=False, sort_keys=True))])
            yield buf.getvalue()

    name = f"connect-audit-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(generate(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
