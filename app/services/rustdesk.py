"""RustDesk client mirror (playbook Phase 7).

Sync: GitHub `releases/latest` for rustdesk/rustdesk (stable only). Assets
are mapped to platforms with ASSET_RULES (names taken from the 1.5.0
`.github/workflows/flutter-build.yml`; re-check on upgrades). Each download
is verified against the SHA-256 `digest` GitHub publishes. A mismatch fails
closed: the old version stays active and `rustdesk.sync_failed` is
audited. Assets without a digest are hashed locally and the release waits
for an admin (pending_approval). Files land in a temporary directory and
are swapped in atomically. The current and one previous version are kept.

Windows exe filename (from RustDesk 1.5.0 src/custom_server.rs):
  rustdesk-host=<host>,key=<key>,relay=<relay>,.exe
The trailing comma is RustDesk's own final delimiter, so a "(1)" added by
Windows to a duplicate download does not garble the key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db import sessionmaker
from ..models import RustdeskRelease, utcnow
from ..rds import redis
from . import settings as settings_svc
from .events import SYSTEM, Actor, emit

log = logging.getLogger("connect.rustdesk")
MAX_ASSET_BYTES = 400 * 1024 * 1024
LAST_CHECK_KEY = "rustdesk:last_check"
# Test hook: an httpx transport (MockTransport) replaces the network.
transport: httpx.AsyncBaseTransport | None = None


@dataclass(frozen=True)
class Rule:
    platform: str   # windows macos linux android
    arch: str       # x86_64 arm64 armv7 universal
    kind: str       # exe msi dmg deb rpm appimage apk
    pattern: str    # regex on the asset name; group "v" is the version
    label: str


V = r"(?P<v>\d+\.\d+\.\d+)"
ASSET_RULES: list[Rule] = [
    Rule("windows", "x86_64", "exe", rf"^rustdesk-{V}-x86_64\.exe$", "Windows (64-bit)"),
    Rule("windows", "x86_64", "msi", rf"^rustdesk-{V}-x86_64\.msi$", "Windows installer (MSI, 64-bit)"),
    Rule("windows", "arm64", "exe", rf"^rustdesk-{V}-aarch64\.exe$", "Windows on Arm"),
    Rule("macos", "arm64", "dmg", rf"^rustdesk-{V}-aarch64\.dmg$", "Mac with Apple chip (M1 or later)"),
    Rule("macos", "x86_64", "dmg", rf"^rustdesk-{V}-x86_64\.dmg$", "Mac with Intel processor"),
    Rule("linux", "x86_64", "deb", rf"^rustdesk-{V}-x86_64\.deb$", "Ubuntu or Debian (64-bit)"),
    Rule("linux", "arm64", "deb", rf"^rustdesk-{V}-aarch64\.deb$", "Ubuntu or Debian (Arm 64-bit)"),
    Rule("linux", "x86_64", "rpm", rf"^rustdesk-{V}-\d+\.x86_64\.rpm$", "Fedora or Red Hat (64-bit)"),
    Rule("linux", "arm64", "rpm", rf"^rustdesk-{V}-\d+\.aarch64\.rpm$", "Fedora or Red Hat (Arm 64-bit)"),
    Rule("linux", "x86_64", "appimage", rf"^rustdesk-{V}-x86_64\.AppImage$", "Other Linux (AppImage, 64-bit)"),
    Rule("linux", "arm64", "appimage", rf"^rustdesk-{V}-aarch64\.AppImage$", "Other Linux (AppImage, Arm 64-bit)"),
    Rule("android", "universal", "apk", rf"^rustdesk-{V}-universal\.apk$", "Android"),
    Rule("android", "arm64", "apk", rf"^rustdesk-{V}-aarch64\.apk$", "Android (Arm 64-bit only)"),
    Rule("android", "armv7", "apk", rf"^rustdesk-{V}-armv7\.apk$", "Android (older 32-bit phones)"),
]
# Preferred download per platform/arch for the big Download button.
PREFERRED = {
    ("windows", "x86_64"): "exe", ("windows", "arm64"): "exe", ("macos", "arm64"): "dmg",
    ("macos", "x86_64"): "dmg", ("linux", "x86_64"): "deb", ("linux", "arm64"): "deb",
    ("android", "universal"): "apk", ("android", "arm64"): "apk", ("android", "armv7"): "apk",
}
INVALID_FILENAME = set('\\/:*?"<>|,') | {chr(c) for c in range(32)}


def map_assets(assets: list[dict]) -> list[dict]:
    """Map GitHub release assets to our platform table. Unknown, sciter,
    suse, flatpak, unattended and other variants are ignored."""
    out = []
    for a in assets:
        for r in ASSET_RULES:
            if re.match(r.pattern, a.get("name", "")):
                out.append({"platform": r.platform, "arch": r.arch, "kind": r.kind, "label": r.label,
                            "filename": a["name"], "size": a.get("size"), "url": a.get("browser_download_url"),
                            "digest": a.get("digest")})
                break
    return out


def windows_filename(host: str, key: str, relay: str = "") -> tuple[str, str | None]:
    """Returns (filename, problem). problem is None when the config fits."""
    if not host:
        return "rustdesk.exe", "No RustDesk host is configured."
    parts = [f"host={host}"]
    if key:
        parts.append(f"key={key}")
    if relay and relay != host:
        parts.append(f"relay={relay}")
    for v in [host, key, relay]:
        bad = sorted(set(v or "") & INVALID_FILENAME)
        if bad:
            return "rustdesk.exe", ("The RustDesk key or host contains characters that can't be used in a "
                                    f"Windows file name ({' '.join(repr(b) for b in bad)}). Customers will get "
                                    "the plain installer and must enter the server details by hand.")
    name = "rustdesk-" + ",".join(parts) + ",.exe"
    if len(name) > 200:
        return "rustdesk.exe", "The RustDesk server details are too long for a file name."
    return name, None


def release_dir(version: str) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(version)
    return get_config().rustdesk_dir / version


def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "connect-rustdesk-mirror"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return httpx.AsyncClient(transport=transport, headers=headers, follow_redirects=True,
                             timeout=httpx.Timeout(30, read=120))


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> str:
    h = hashlib.sha256()
    size = 0
    async with client.stream("GET", url) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 20):
                size += len(chunk)
                if size > MAX_ASSET_BYTES:
                    raise ValueError(f"{dest.name} is larger than {MAX_ASSET_BYTES // (1 << 20)} MB")
                h.update(chunk)
                f.write(chunk)
    return h.hexdigest()


def authenticode(path: Path) -> str:
    """Record what osslsigncode says about a Windows binary's signature."""
    try:
        p = subprocess.run(["osslsigncode", "verify", "-in", str(path)], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return "not checked (osslsigncode not installed)"
    except subprocess.TimeoutExpired:
        return "not checked (timeout)"
    text = (p.stdout + p.stderr)
    signer = re.search(r"Subject: (.+)", text)
    if p.returncode == 0:
        return "valid" + (f": {signer.group(1).strip()}" if signer else "")
    if "No signature found" in text:
        return "unsigned"
    return "present, not verified" + (f": {signer.group(1).strip()}" if signer else "")


async def _set_last_check(result: dict) -> None:
    await redis().set(LAST_CHECK_KEY, json.dumps({"at": int(time.time()), **result}))


async def last_check() -> dict | None:
    v = await redis().get(LAST_CHECK_KEY)
    return json.loads(v) if v else None


async def releases(db: AsyncSession) -> list[RustdeskRelease]:
    rows = (await db.scalars(select(RustdeskRelease))).all()
    return sorted(rows, key=lambda r: tuple(int(x) for x in r.version.split(".")), reverse=True)


async def active(db: AsyncSession) -> RustdeskRelease | None:
    return await db.scalar(select(RustdeskRelease).where(RustdeskRelease.status == "active"))


async def _activate(db: AsyncSession, rel: RustdeskRelease, keep: int) -> None:
    for r in await releases(db):
        if r.version != rel.version and r.status == "active":
            r.status = "previous"
            r.pinned = False
    rel.status = "active"
    await db.flush()
    await _prune(db, keep)


async def _prune(db: AsyncSession, keep: int) -> None:
    """Keep the active version and the newest (keep - 1) previous ones."""
    kept = 0
    for r in await releases(db):
        if r.status == "active":
            continue
        if r.status == "previous" and kept < keep - 1:
            kept += 1
            continue
        if r.status in ("previous",):
            r.status = "archived"
            shutil.rmtree(release_dir(r.version), ignore_errors=True)


async def sync(force: bool = False, actor: Actor = SYSTEM) -> dict:
    cfg = get_config()
    async with sessionmaker()() as db:
        st = await settings_svc.load(db, fresh=True)
        keep = int(st["rustdesk"].get("keep_versions", 2))
        try:
            async with _client() as client:
                r = await client.get(f"{cfg.github_api}/repos/{cfg.rustdesk_repo}/releases/latest")
                r.raise_for_status()
                rel = r.json()
                if rel.get("prerelease") or rel.get("draft"):
                    raise ValueError(f"latest release {rel.get('tag_name')} is not a stable release")
                tag = rel["tag_name"]
                version = tag.lstrip("v")
                if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                    raise ValueError(f"unexpected tag {tag!r}")
                existing = await db.get(RustdeskRelease, version)
                if existing and existing.status in ("active", "previous", "pending_approval") and not force:
                    await _set_last_check({"ok": True, "version": version, "result": "up to date"})
                    return {"version": version, "result": "up to date"}
                mapped = map_assets(rel.get("assets", []))
                if not mapped:
                    raise ValueError("no recognised assets in the release (asset names changed?)")
                final = release_dir(version)
                tmp = final.with_name(f".tmp-{version}-{int(time.time())}")
                tmp.mkdir(parents=True, exist_ok=True)
                needs_approval = False
                try:
                    for a in mapped:
                        dest = tmp / a["filename"]
                        actual = await _download(client, a["url"], dest)
                        digest = a.pop("digest", None)
                        a["sha256"] = actual
                        if digest and digest.startswith("sha256:"):
                            if digest.split(":", 1)[1].lower() != actual:
                                raise ValueError(f"SHA-256 mismatch for {a['filename']}")
                            a["verified_by"] = "github_digest"
                        else:
                            a["verified_by"] = "computed_only"
                            needs_approval = True
                        if a["kind"] in ("exe", "msi"):
                            a["authenticode"] = await asyncio.to_thread(authenticode, dest)
                        a.pop("url", None)
                except Exception:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise
                # Atomic swap into place.
                if final.exists():
                    shutil.rmtree(final)
                os.replace(tmp, final)
        except Exception as e:
            msg = str(e)[:500]
            log.warning("rustdesk sync failed: %s", msg)
            await emit(db, "rustdesk.sync_failed", actor, data={"error": msg})
            await _set_last_check({"ok": False, "error": msg})
            return {"error": msg}

        rel_row = existing or RustdeskRelease(version=version, tag=tag, status="pending_approval")
        rel_row.tag = tag
        rel_row.published_at = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00")) if rel.get("published_at") else None
        rel_row.fetched_at = utcnow()
        rel_row.assets = mapped
        rel_row.error = None
        if existing is None:
            db.add(rel_row)
        pinned = await db.scalar(select(RustdeskRelease).where(RustdeskRelease.pinned.is_(True)))
        if needs_approval:
            rel_row.status = "pending_approval"
            result = "waiting for admin approval (no GitHub digest)"
        elif pinned and pinned.version != version:
            rel_row.status = "previous"
            result = f"downloaded; not activated because {pinned.version} is pinned"
        else:
            await _activate(db, rel_row, keep)
            result = "activated"
        await db.flush()
        await emit(db, "rustdesk.synced", actor, data={"version": version, "result": result,
                                                       "assets": len(mapped)})
        await _set_last_check({"ok": True, "version": version, "result": result})
        return {"version": version, "result": result}


async def sync_if_due() -> None:
    async with sessionmaker()() as db:
        st = await settings_svc.load(db, fresh=True)
    hours = float(st["rustdesk"].get("sync_interval_hours", 6))
    lc = await last_check()
    if lc and time.time() - lc["at"] < hours * 3600:
        return
    await sync()


# ---------------------------------------------------------------- admin actions

async def approve(db: AsyncSession, version: str, admin) -> None:
    rel = await db.get(RustdeskRelease, version)
    if rel is None or rel.status != "pending_approval":
        raise ValueError("That release isn't waiting for approval.")
    st = await settings_svc.load(db)
    await _activate(db, rel, int(st["rustdesk"].get("keep_versions", 2)))
    await emit(db, "rustdesk.approved", Actor.user(admin), data={"version": version})


async def pin(db: AsyncSession, version: str, admin, on: bool = True) -> None:
    rel = await db.get(RustdeskRelease, version)
    if rel is None or rel.status not in ("active", "previous"):
        raise ValueError("Only the current or previous version can be pinned.")
    for r in await releases(db):
        r.pinned = False
    if on:
        if rel.status != "active":
            st = await settings_svc.load(db)
            await _activate(db, rel, int(st["rustdesk"].get("keep_versions", 2)) + 1)
        rel.pinned = True
    await emit(db, "rustdesk.pinned", Actor.user(admin), data={"version": version, "pinned": on})


async def rollback(db: AsyncSession, admin) -> str:
    current = await active(db)
    prev = next((r for r in await releases(db) if r.status == "previous"), None)
    if prev is None:
        raise ValueError("There is no previous version to roll back to.")
    st = await settings_svc.load(db)
    await _activate(db, prev, int(st["rustdesk"].get("keep_versions", 2)) + 1)
    await emit(db, "rustdesk.rolled_back", Actor.user(admin), data={"from": current.version if current else None,
                                                                     "to": prev.version})
    return prev.version


def downloads_for(rel: RustdeskRelease | None) -> list[dict]:
    if rel is None:
        return []
    return [{"platform": a["platform"], "arch": a["arch"], "kind": a["kind"], "label": a["label"],
             "size": a.get("size")} for a in rel.assets]
