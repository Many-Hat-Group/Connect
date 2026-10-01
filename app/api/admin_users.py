"""Admin: user management (create, deactivate, roles, password and MFA reset)."""
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import websession
from ..auth.passwords import hash_password
from ..db import get_db
from ..models import User
from ..services.events import Actor, emit
from ..web import client_ip, csrf_protect, redirect, render, require_admin

router = APIRouter(prefix="/admin/users")


def temp_password() -> str:
    # 16 chars from an unambiguous alphabet, shown once to the admin.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


async def _get(db: AsyncSession, user_id: str) -> User:
    try:
        u = await db.get(User, uuid.UUID(user_id))
    except ValueError:
        u = None
    if u is None:
        raise HTTPException(404)
    return u


async def _active_admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active))


@router.get("")
async def list_users(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.scalars(select(User).order_by(User.is_active.desc(), User.display_name))).all()
    return await render(request, db, "admin/users.html", {"users": users, "error": None, "notice": None})


@router.post("", dependencies=[Depends(csrf_protect)])
async def create_user(request: Request, email: str = Form(""), display_name: str = Form(""), role: str = Form("tech"),
                      admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    email_n = email.strip().lower()
    error = None
    if "@" not in email_n or len(email_n) > 320:
        error = "Enter a valid email address."
    elif not display_name.strip() or len(display_name) > 120:
        error = "Enter a display name."
    elif role not in ("tech", "admin"):
        error = "Choose a role."
    elif await db.scalar(select(User).where(func.lower(User.email) == email_n)):
        error = "A user with that email already exists."
    if error:
        users = (await db.scalars(select(User).order_by(User.display_name))).all()
        return await render(request, db, "admin/users.html", {"users": users, "error": error, "notice": None},
                            status=400)
    pw = temp_password()
    u = User(email=email_n, display_name=display_name.strip(), role=role, password_hash=hash_password(pw),
             must_change_password=True, auth_provider="local")
    db.add(u)
    await db.flush()
    await emit(db, "user.created", Actor.user(admin), data={"user_id": str(u.id), "email": email_n, "role": role},
               ip=client_ip(request))
    users = (await db.scalars(select(User).order_by(User.display_name))).all()
    return await render(request, db, "admin/users.html", {
        "users": users, "error": None,
        "notice": {"title": f"{u.display_name} has been created",
                   "body": "Give them this temporary password. They must change it and set up an authenticator "
                           "app at first login. It is shown only once.",
                   "secret": pw}})


@router.post("/{user_id}", dependencies=[Depends(csrf_protect)])
async def update_user(request: Request, user_id: str, action: str = Form(...), role: str = Form(""),
                      display_name: str = Form(""), admin: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    u = await _get(db, user_id)
    actor = Actor.user(admin)
    ip = client_ip(request)
    notice = None
    error = None
    if action in ("deactivate", "demote") and u.role == "admin" and u.is_active and await _active_admins(db) <= 1:
        error = "You can't remove the last active admin."
    elif action == "deactivate":
        u.is_active = False
        await emit(db, "user.updated", actor, data={"user_id": str(u.id), "change": "deactivated"}, ip=ip)
        await websession.destroy_user(str(u.id))
    elif action == "activate":
        u.is_active = True
        await emit(db, "user.updated", actor, data={"user_id": str(u.id), "change": "activated"}, ip=ip)
    elif action == "role" and role in ("tech", "admin"):
        if u.role == "admin" and role == "tech" and await _active_admins(db) <= 1:
            error = "You can't remove the last active admin."
        else:
            old, u.role = u.role, role
            await emit(db, "user.updated", actor, data={"user_id": str(u.id), "change": "role",
                                                         "from": old, "to": role}, ip=ip)
            await websession.destroy_user(str(u.id))
    elif action == "rename" and display_name.strip() and len(display_name) <= 120:
        old, u.display_name = u.display_name, display_name.strip()
        await emit(db, "user.updated", actor, data={"user_id": str(u.id), "change": "display_name",
                                                     "from": old, "to": u.display_name}, ip=ip)
    elif action == "reset_mfa":
        u.totp_secret = None
        u.totp_enrolled_at = None
        u.recovery_codes = None
        await emit(db, "user.mfa_reset", actor, data={"user_id": str(u.id)}, ip=ip)
        await websession.destroy_user(str(u.id))
        notice = {"title": f"Authenticator reset for {u.display_name}",
                  "body": "They will be asked to set up a new authenticator app at their next login."}
    elif action == "reset_password":
        pw = temp_password()
        u.password_hash = hash_password(pw)
        u.must_change_password = True
        await emit(db, "user.password_reset", actor, data={"user_id": str(u.id)}, ip=ip)
        await websession.destroy_user(str(u.id))
        notice = {"title": f"Password reset for {u.display_name}",
                  "body": "Give them this temporary password. It is shown only once.", "secret": pw}
    else:
        raise HTTPException(400)
    await db.commit()
    if notice or error:
        users = (await db.scalars(select(User).order_by(User.display_name))).all()
        return await render(request, db, "admin/users.html", {"users": users, "error": error, "notice": notice},
                            status=400 if error else 200)
    return redirect("/admin/users")
