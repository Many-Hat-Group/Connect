"""Portal login, mandatory TOTP enrolment, recovery codes, logout, password."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import base as providers
from ..auth import local  # noqa: F401  (registers the local provider)
from ..auth import ratelimit, totp, websession
from ..auth.passwords import hash_password, policy_error, verify_password
from ..db import get_db
from ..models import User, utcnow
from ..rds import redis
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import (
    Redirect, clear_session_cookie, client_ip, csrf_protect, current_websession, ensure_websession,
    redirect, render, require_user, set_session_cookie,
)

router = APIRouter()
GENERIC_LOGIN_ERROR = "That email and password combination didn't work."


def _mins(seconds: int) -> int:
    return max(1, (seconds + 59) // 60)


async def _limits(db) -> dict:
    return (await settings_svc.load(db))["limits"]


@router.get("/login")
async def login_form(request: Request, db: AsyncSession = Depends(get_db)):
    s = await ensure_websession(request)
    if s.is_full:
        return redirect("/portal")
    return await render(request, db, "auth/login.html", {"error": None, "email": ""})


@router.post("/login", dependencies=[Depends(csrf_protect)])
async def login(request: Request, email: str = Form(""), password: str = Form(""),
                db: AsyncSession = Depends(get_db)):
    s = await ensure_websession(request)
    ip = client_ip(request) or "unknown"
    email_n = email.strip().lower()[:320]
    lim = await _limits(db)
    ip_key, acct_key = f"login:ip:{ip}", f"login:acct:{email_n}"

    wait = max(await ratelimit.locked(ip_key), await ratelimit.locked(acct_key))
    if wait:
        await emit(db, "auth.login_failed", Actor("system"), data={"email": email_n, "reason": "locked"}, ip=ip)
        return await render(request, db, "auth/login.html", {
            "error": f"Too many attempts. Try again in {_mins(wait)} minutes.", "email": email}, status=429)

    user = None
    for p in providers.password_providers():
        user = await p.authenticate(db, email_n, password)
        if user:
            break

    if user is None:
        n_ip = await ratelimit.hit(ip_key, lim["login_window_seconds"])
        n_acct = await ratelimit.hit(acct_key, lim["login_window_seconds"])
        await emit(db, "auth.login_failed", Actor("system"), data={"email": email_n, "reason": "credentials"}, ip=ip)
        for key, n, cap, scope in ((ip_key, n_ip, lim["login_fail_per_ip"], "ip"),
                                   (acct_key, n_acct, lim["login_fail_per_account"], "account")):
            if n >= cap:
                await ratelimit.lock(key, lim["login_lockout_seconds"])
                await ratelimit.reset(key)
                await emit(db, "auth.locked_out", Actor("system"),
                           data={"scope": scope, "email": email_n, "seconds": lim["login_lockout_seconds"]}, ip=ip)
        return await render(request, db, "auth/login.html", {"error": GENERIC_LOGIN_ERROR, "email": email}, status=401)

    await ratelimit.reset(acct_key)
    await db.commit()  # persist any password rehash
    stage = "mfa" if user.totp_enrolled_at else "enrol"
    s = await websession.rotate(s, stage, str(user.id))
    resp = redirect("/login/mfa" if stage == "mfa" else "/mfa/enrol")
    set_session_cookie(resp, s)
    return resp


# ---------------------------------------------------------------- MFA

async def _pending_user(request: Request, db: AsyncSession, stage: str) -> tuple[websession.WebSession, User]:
    s = await current_websession(request)
    if s is None or s.user_id is None:
        raise Redirect("/login")
    if s.stage == "full":
        raise Redirect("/portal")
    if s.stage != stage:
        raise Redirect("/login/mfa" if s.stage == "mfa" else "/mfa/enrol")
    user = await db.get(User, s.user_id)
    if user is None or not user.is_active:
        await websession.destroy(s)
        raise Redirect("/login")
    return s, user


async def _finish_login(request, db, s, user, method: str):
    user.last_login_at = utcnow()
    await db.commit()
    n = await websession.rotate(s, "full", str(user.id))
    await emit(db, "auth.login", Actor.user(user), data={"method": method}, ip=client_ip(request))
    return n


@router.get("/login/mfa")
async def mfa_form(request: Request, db: AsyncSession = Depends(get_db)):
    await _pending_user(request, db, "mfa")
    return await render(request, db, "auth/mfa.html", {"error": None})


@router.post("/login/mfa", dependencies=[Depends(csrf_protect)])
async def mfa_submit(request: Request, code: str = Form(""), db: AsyncSession = Depends(get_db)):
    s, user = await _pending_user(request, db, "mfa")
    ip = client_ip(request)
    lim = await _limits(db)
    key = f"mfa:acct:{user.id}"
    if await ratelimit.locked(key):
        await websession.destroy(s)
        raise Redirect("/login")

    method = None
    last = await redis().get(f"totp:last:{user.id}")
    step = totp.verify_code(totp.decrypt_secret(user.totp_secret), code, int(last) if last else None)
    if step is not None:
        await redis().set(f"totp:last:{user.id}", step, ex=300)
        method = "totp"
    else:
        remaining = totp.use_recovery_code(user.recovery_codes, code)
        if remaining is not None:
            user.recovery_codes = remaining
            method = "recovery_code"

    if method is None:
        n = await ratelimit.hit(key, lim["login_window_seconds"])
        await emit(db, "auth.mfa_failed", Actor.user(user), ip=ip)
        if n >= lim["login_fail_per_account"]:
            await ratelimit.lock(key, lim["login_lockout_seconds"])
            await ratelimit.reset(key)
            await emit(db, "auth.locked_out", Actor.user(user), data={"scope": "mfa"}, ip=ip)
            await websession.destroy(s)
            resp = redirect("/login")
            clear_session_cookie(resp)
            return resp
        return await render(request, db, "auth/mfa.html", {"error": "That code didn't work. Try again."}, status=401)

    await ratelimit.reset(key)
    if method == "recovery_code":
        await emit(db, "auth.recovery_code_used", Actor.user(user), data={"remaining": len(user.recovery_codes)},
                   ip=ip, commit=False)
    n = await _finish_login(request, db, s, user, method)
    resp = redirect("/portal")
    set_session_cookie(resp, n)
    return resp


async def _issuer(db) -> str:
    brand = (await settings_svc.load(db))["branding"]
    return f'{brand["company_short"]} {brand["product_name"]}'.strip()


@router.get("/mfa/enrol")
async def enrol_form(request: Request, db: AsyncSession = Depends(get_db)):
    s, user = await _pending_user(request, db, "enrol")
    if "pending_totp" not in s.data:
        s.data["pending_totp"] = totp.encrypt_secret(totp.new_secret())
        await websession.save(s)
    secret = totp.decrypt_secret(s.data["pending_totp"])
    uri = totp.provisioning_uri(secret, user.email, await _issuer(db))
    return await render(request, db, "auth/enrol.html", {
        "qr_svg": totp.qr_svg(uri), "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "error": None})


@router.post("/mfa/enrol", dependencies=[Depends(csrf_protect)])
async def enrol_submit(request: Request, code: str = Form(""), db: AsyncSession = Depends(get_db)):
    s, user = await _pending_user(request, db, "enrol")
    enc = s.data.get("pending_totp")
    if not enc:
        raise Redirect("/mfa/enrol")
    secret = totp.decrypt_secret(enc)
    step = totp.verify_code(secret, code)
    if step is None:
        await emit(db, "auth.mfa_failed", Actor.user(user), data={"stage": "enrol"}, ip=client_ip(request))
        return await render(request, db, "auth/enrol.html", {
            "qr_svg": totp.qr_svg(totp.provisioning_uri(secret, user.email, await _issuer(db))),
            "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
            "error": "That code didn't work. Check the time on your phone and try again."}, status=400)
    codes = totp.new_recovery_codes()
    user.totp_secret = enc
    user.totp_enrolled_at = utcnow()
    user.recovery_codes = totp.hash_recovery_codes(codes)
    await redis().set(f"totp:last:{user.id}", step, ex=300)
    s.data.pop("pending_totp", None)
    s.data["show_recovery"] = codes
    await emit(db, "auth.mfa_enrolled", Actor.user(user), ip=client_ip(request), commit=False)
    n = await _finish_login(request, db, s, user, "totp_enrolment")
    resp = redirect("/mfa/recovery-codes")
    set_session_cookie(resp, n)
    return resp


@router.get("/mfa/recovery-codes")
async def recovery_codes(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    s = await current_websession(request)
    codes = s.data.pop("show_recovery", None)
    await websession.save(s)
    if not codes:
        raise Redirect("/portal")
    nxt = s.data.get("after_recovery", "/portal")
    return await render(request, db, "auth/recovery.html", {"codes": codes, "next": nxt})


@router.post("/account/recovery-codes", dependencies=[Depends(csrf_protect)])
async def regenerate_recovery(request: Request, code: str = Form(""), user: User = Depends(require_user),
                              db: AsyncSession = Depends(get_db)):
    last = await redis().get(f"totp:last:{user.id}")
    step = totp.verify_code(totp.decrypt_secret(user.totp_secret), code, int(last) if last else None)
    if step is None:
        await emit(db, "auth.mfa_failed", Actor.user(user), data={"stage": "regenerate_recovery"}, ip=client_ip(request))
        return await render(request, db, "auth/account.html", {"error": "That code didn't work.", "ok": None}, status=400)
    await redis().set(f"totp:last:{user.id}", step, ex=300)
    codes = totp.new_recovery_codes()
    user.recovery_codes = totp.hash_recovery_codes(codes)
    await emit(db, "user.updated", Actor.user(user), data={"user_id": str(user.id), "change": "recovery_codes"},
               ip=client_ip(request))
    s = await current_websession(request)
    s.data["show_recovery"] = codes
    s.data["after_recovery"] = "/account"
    await websession.save(s)
    return redirect("/mfa/recovery-codes")


# ---------------------------------------------------------------- account

@router.post("/logout", dependencies=[Depends(csrf_protect)])
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    s = await current_websession(request)
    if s and s.user_id:
        user = await db.get(User, s.user_id)
        if user:
            await emit(db, "auth.logout", Actor.user(user), ip=client_ip(request))
    if s:
        await websession.destroy(s)
    resp = redirect("/login")
    clear_session_cookie(resp)
    return resp


@router.get("/account")
async def account(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await render(request, db, "auth/account.html", {"error": None, "ok": None})


@router.get("/account/password")
async def password_form(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await render(request, db, "auth/password.html", {"error": None, "forced": user.must_change_password})


@router.post("/account/password", dependencies=[Depends(csrf_protect)])
async def password_change(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form(""),
                          user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    forced = user.must_change_password
    error = None
    if not verify_password(user.password_hash, current):
        error = "Your current password is not right."
    elif new != confirm:
        error = "The new passwords don't match."
    elif new == current:
        error = "Choose a password you haven't used here."
    else:
        error = policy_error(new, user.email)
    if error:
        return await render(request, db, "auth/password.html", {"error": error, "forced": forced}, status=400)
    user.password_hash = hash_password(new)
    user.must_change_password = False
    await emit(db, "auth.password_changed", Actor.user(user), ip=client_ip(request))
    s = await current_websession(request)
    await websession.destroy_user(str(user.id), except_sid=s.sid)
    return redirect("/portal" if forced else "/account?changed=1")
