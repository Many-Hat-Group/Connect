"""Tech JSON API (dashboard and session view). CSRF via X-CSRF-Token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..services import sessions
from ..services import settings as settings_svc
from ..web import client_ip, csrf_protect, require_user

router = APIRouter(prefix="/api")


@router.post("/sessions", dependencies=[Depends(csrf_protect)])
async def create_session(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    s, jc = await sessions.create(db, user, st, ip=client_ip(request))
    base = st["urls"]["connect_url"].rstrip("/")
    return JSONResponse({"session_id": str(s.id), "code": jc.code, "expires_at": jc.expires_at.isoformat(),
                         "link": f"{base}/join/{jc.code}", "ttl": st["timeouts"]["code_ttl_seconds"]}, status_code=201)
