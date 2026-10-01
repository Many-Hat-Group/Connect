"""System health checks for the admin page (playbook Phase 8)."""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import struct
import time
from pathlib import Path

import httpx
from sqlalchemy import text

from .. import db
from ..config import get_config
from ..rds import redis

STUN_MAGIC = 0x2112A442


def stun_probe(host: str, port: int = 3478, timeout: float = 2.0) -> tuple[bool, str]:
    """Send a STUN Binding Request over UDP and check for a matching response."""
    tid = secrets.token_bytes(12)
    req = struct.pack("!HHI", 0x0001, 0, STUN_MAGIC) + tid
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    except socket.gaierror as e:
        return False, f"cannot resolve {host}: {e}"
    addr = infos[0][4]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(req, addr)
            data, _ = s.recvfrom(2048)
        except (socket.timeout, OSError) as e:
            return False, f"no STUN reply from {addr[0]}:{port} ({e.__class__.__name__})"
    if len(data) >= 20 and data[4:8] == struct.pack("!I", STUN_MAGIC) and data[8:20] == tid:
        return True, f"STUN reply from {addr[0]}:{port}"
    return False, "unexpected reply"


async def _check(name: str, coro) -> dict:
    t = time.monotonic()
    try:
        ok, detail = await asyncio.wait_for(coro, timeout=5)
    except Exception as e:
        ok, detail = False, f"{e.__class__.__name__}: {e}"[:200]
    return {"name": name, "ok": ok, "detail": detail, "ms": int((time.monotonic() - t) * 1000)}


async def _postgres():
    async with db.engine().connect() as c:
        v = (await c.execute(text("show server_version"))).scalar()
    return True, f"PostgreSQL {v}"


async def _redis():
    info = await redis().info("server")
    return True, f"Redis {info.get('redis_version')}"


async def _http(url: str, expect: str):
    async with httpx.AsyncClient(timeout=3) as c:
        r = await c.get(url)
    return r.status_code == 200 and expect in r.text, f"HTTP {r.status_code}"


async def _worker():
    v = await redis().get("worker:heartbeat")
    if not v:
        return False, "no heartbeat"
    age = int(time.time()) - int(v)
    return age < 120, f"last heartbeat {age} s ago"


async def _turn(host: str):
    ok, detail = await asyncio.to_thread(stun_probe, host)
    return ok, detail


def disk(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        try:
            u = shutil.disk_usage(p)
        except FileNotFoundError:
            continue
        out.append({"path": str(p), "total_gb": round(u.total / 1e9, 1), "used_gb": round(u.used / 1e9, 1),
                    "free_gb": round(u.free / 1e9, 1), "percent": round(100 * u.used / u.total) if u.total else 0})
    return out


def latest_backup(path: Path) -> dict | None:
    try:
        dumps = sorted(path.glob("connect-*.dump"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not dumps:
        return None
    p = dumps[-1]
    return {"name": p.name, "age_hours": round((time.time() - p.stat().st_mtime) / 3600, 1),
            "size_mb": round(p.stat().st_size / 1e6, 1), "count": len(dumps)}


async def run(turn_host: str) -> list[dict]:
    checks = [
        _check("Database", _postgres()),
        _check("Redis", _redis()),
        _check("Router", _http(os.environ.get("ROUTER_HEALTH_URL", "http://router:8080/healthz"), "ok")),
        _check("Signal server", _http(os.environ.get("SIGNAL_HEALTH_URL", "http://signal:8443/"), "VDONinja")),
        _check("Worker", _worker()),
        _check(f"TURN (STUN probe to {turn_host})", _turn(turn_host)),
    ]
    return list(await asyncio.gather(*checks))
