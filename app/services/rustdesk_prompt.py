"""RustDesk remote-control prompt on the customer page (playbook Phase 7)."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session
from ..rds import redis
from ..ws.hub import publish
from . import rustdesk, sessions
from . import settings as settings_svc
from .detection import Device
from .events import Actor, emit


def choose(d: Device) -> dict:
    """Pick the download for a device. Returns platform/arch, whether the
    customer must say Apple chip or Intel, or that it isn't available."""
    if d.os in ("ios", "ipados"):
        return {"available": False, "reason": "Remote control isn't available on iPhone or iPad."}
    if d.os == "windows":
        return {"available": True, "platform": "windows", "arch": "arm64" if d.arch == "arm64" else "x86_64"}
    if d.os == "macos":
        if d.arch_certain and d.arch in ("arm64", "x86_64"):
            return {"available": True, "platform": "macos", "arch": d.arch}
        # Client Hints unavailable (Safari, Firefox): ask, default Apple chip.
        return {"available": True, "platform": "macos", "arch": "arm64", "ask_mac_chip": True}
    if d.os in ("linux", "chromeos"):
        return {"available": d.os == "linux", "platform": "linux", "arch": "arm64" if d.arch == "arm64" else "x86_64",
                **({} if d.os == "linux" else {"reason": "Remote control isn't available on a Chromebook."})}
    if d.os == "android":
        return {"available": True, "platform": "android", "arch": "universal"}
    return {"available": True, "platform": None, "arch": None}


async def remember_device(session_id: str, d: Device) -> None:
    await redis().set(f"device:{session_id}", json.dumps(d.to_dict()), ex=12 * 3600)


async def device_for(s: Session) -> Device:
    raw = await redis().get(f"device:{s.id}")
    if raw:
        return Device(**json.loads(raw))
    from .detection import detect
    return detect(s.user_agent or "")


async def prompt(db: AsyncSession, s: Session, user, ip) -> dict:
    st = await settings_svc.load(db)
    rd = st["rustdesk"]
    rel = await rustdesk.active(db)
    d = await device_for(s)
    choice = choose(d)
    msg = {"type": "rustdesk", "company": st["branding"]["company_name"], "host": rd["host"], "key": rd["key"],
           "relay": rd["relay"], "choice": choice, "downloads": rustdesk.downloads_for(rel),
           "version": rel.version if rel else None, "tech": user.display_name}
    await emit(db, "session.rustdesk_prompted", Actor.user(user), s.id,
               {"suggested": choice, "version": msg["version"]}, ip=ip)
    await publish(f"ch:customer:{s.id}", msg)
    return {"choice": choice, "version": msg["version"], "available_assets": len(msg["downloads"])}


async def customer_response(db: AsyncSession, s: Session, msg: dict) -> None:
    if msg.get("action") == "declined":
        await emit(db, "session.rustdesk_declined", Actor.customer(s.id), s.id, {})
        await sessions.notify(db, s, {"type": "session.update"}, toast={
            "title": f"{s.customer_name} declined remote control", "body": s.customer_company or "",
            "session": str(s.id)})
