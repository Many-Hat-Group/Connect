"""RustDesk: tech prompt, customer downloads, admin mirror management."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Session, User
from ..services import customer_auth, permissions, rustdesk, rustdesk_prompt, sessions
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import client_ip, csrf_protect, redirect, render, require_admin, require_user

router = APIRouter()


@router.post("/api/sessions/{session_id}/rustdesk", dependencies=[Depends(csrf_protect)])
async def request_remote(request: Request, session_id: str, user: User = Depends(require_user),
                         db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id, need="manage")
    if s.status == "ended":
        raise HTTPException(410)
    return await rustdesk_prompt.prompt(db, s, user, client_ip(request))


@router.get("/dl/rustdesk/{platform}/{arch}")
async def download(request: Request, platform: str, arch: str, kind: str = "", db: AsyncSession = Depends(get_db)):
    sid = await customer_auth.from_request(request)
    if not sid:
        raise HTTPException(404)
    s = await db.get(Session, sessions.parse_uuid(sid))
    if s is None or s.status == "ended":
        raise HTTPException(404)
    rel = await rustdesk.active(db)
    if rel is None:
        raise HTTPException(404)
    kind = kind or rustdesk.PREFERRED.get((platform, arch), "")
    asset = next((a for a in rel.assets if a["platform"] == platform and a["arch"] == arch and a["kind"] == kind), None)
    if asset is None:
        raise HTTPException(404)
    path = rustdesk.release_dir(rel.version) / asset["filename"]
    if not path.is_file():
        raise HTTPException(404)
    filename = asset["filename"]
    if platform == "windows" and kind == "exe":
        rd = (await settings_svc.load(db))["rustdesk"]
        filename, _problem = rustdesk.windows_filename(rd["host"], rd["key"], rd["relay"])
    await emit(db, "session.rustdesk_downloaded", Actor.customer(s.id), s.id,
               {"platform": platform, "arch": arch, "kind": kind, "version": rel.version, "filename": filename},
               ip=client_ip(request))
    await sessions.notify(db, s, {"type": "session.update"}, toast={
        "title": f"{s.customer_name} is downloading RustDesk", "body": f"{asset['label']}, version {rel.version}",
        "session": str(s.id)})
    from urllib.parse import quote
    # Plain filename= for every browser plus RFC 5987 filename*. The name is
    # ASCII and contains no quotes (checked in windows_filename).
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Content-Disposition": disposition, "Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- admin

@router.get("/admin/rustdesk")
async def admin_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    rd = st["rustdesk"]
    _, problem = rustdesk.windows_filename(rd["host"], rd["key"], rd["relay"])
    last = await rustdesk.last_check()
    if last:
        from datetime import datetime, timezone
        last["iso"] = datetime.fromtimestamp(last["at"], timezone.utc).isoformat()
    return await render(request, db, "admin/rustdesk.html", {
        "releases": await rustdesk.releases(db), "last": last, "rd": rd,
        "filename_problem": problem, "exe_name": rustdesk.windows_filename(rd["host"], rd["key"], rd["relay"])[0],
        "message": request.query_params.get("m")})


@router.post("/admin/rustdesk/{action}", dependencies=[Depends(csrf_protect)])
async def admin_action(request: Request, action: str, version: str = "", admin: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    form = await request.form()
    version = str(form.get("version") or version)
    try:
        if action == "check":
            res = await rustdesk.sync(force=bool(form.get("force")), actor=Actor.user(admin))
            msg = res.get("error") and f"Check failed: {res['error']}" or f"{res['version']}: {res['result']}"
        elif action == "approve":
            await rustdesk.approve(db, version, admin); await db.commit(); msg = f"{version} approved and active"
        elif action == "pin":
            await rustdesk.pin(db, version, admin, True); await db.commit(); msg = f"{version} pinned"
        elif action == "unpin":
            await rustdesk.pin(db, version, admin, False); await db.commit(); msg = "Unpinned; new releases activate automatically"
        elif action == "rollback":
            v = await rustdesk.rollback(db, admin); await db.commit(); msg = f"Rolled back to {v}"
        else:
            raise HTTPException(404)
    except ValueError as e:
        msg = str(e)
    from urllib.parse import quote
    return redirect(f"/admin/rustdesk?m={quote(msg)}")
