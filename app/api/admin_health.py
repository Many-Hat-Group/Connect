"""Admin: system health (playbook Phase 8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db import get_db
from ..models import User
from ..services import health, rustdesk, turn_creds
from ..services import settings as settings_svc
from ..web import render, require_admin

router = APIRouter(prefix="/admin")


@router.get("/health")
async def health_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    cfg = get_config()
    turn_host = cfg.turn_host or st["turn"]["host"]
    checks = await health.run(turn_host)
    rel = await rustdesk.active(db)
    return await render(request, db, "admin/health.html", {
        "checks": checks, "disk": health.disk([cfg.media_dir, cfg.rustdesk_dir, cfg.backup_dir]),
        "backup": health.latest_backup(cfg.backup_dir), "rustdesk_last": await rustdesk.last_check(),
        "rustdesk_active": rel.version if rel else None, "turn_host": turn_host})


@router.get("/health/turn-test")
async def turn_test_config(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Short-lived credentials for the relay-only ICE test in the admin's browser."""
    st = await settings_svc.load(db)
    host = get_config().turn_host or st["turn"]["host"]
    user, cred = turn_creds.credentials(f"healthcheck-{admin.id}", ttl=300)
    return {"iceServers": [
        {"urls": [f"turn:{host}:3478?transport=udp"], "username": user, "credential": cred},
        {"urls": [f"turn:{host}:3478?transport=tcp"], "username": user, "credential": cred},
        {"urls": [f"turns:{host}:5349?transport=tcp"], "username": user, "credential": cred},
    ]}
