"""Runtime settings: defaults.yaml seeded into the `settings` table.

Each top-level section of defaults.yaml (branding, urls, turn, ...) is one row,
keyed by section name, value = dict. Reads are cached per process for a few
seconds; writes publish an invalidation over Redis so every process reloads.
"""
from __future__ import annotations

import copy
import time
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import APP_DIR
from ..models import Setting, utcnow

DEFAULTS_FILE = APP_DIR / "defaults.yaml"
CACHE_SECONDS = 5.0
_cache: dict[str, Any] | None = None
_cache_at = 0.0


def defaults() -> dict[str, Any]:
    with open(DEFAULTS_FILE) as f:
        return yaml.safe_load(f)


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


async def seed(db: AsyncSession) -> None:
    """Insert any missing sections. Existing values are never overwritten;
    new keys added to defaults.yaml appear through the merge in load()."""
    for key, value in defaults().items():
        await db.execute(insert(Setting).values(key=key, value=value).on_conflict_do_nothing())
    await db.execute(insert(Setting).values(key="setup", value={"complete": False}).on_conflict_do_nothing())
    await db.commit()


async def load(db: AsyncSession, fresh: bool = False) -> dict[str, Any]:
    global _cache, _cache_at
    if not fresh and _cache is not None and time.monotonic() - _cache_at < CACHE_SECONDS:
        return _cache
    rows = (await db.execute(select(Setting))).scalars().all()
    stored = {r.key: r.value for r in rows}
    merged = _merge(defaults(), stored)
    merged.setdefault("setup", {"complete": False})
    _cache, _cache_at = merged, time.monotonic()
    return merged


def invalidate() -> None:
    global _cache
    _cache = None


async def update(db: AsyncSession, key: str, changes: dict[str, Any], user_id=None) -> dict[str, Any]:
    """Merge `changes` into section `key`; returns {field: [old, new]} for audit."""
    current = (await load(db, fresh=True)).get(key, {})
    new = _merge(current, changes)
    diff = {k: [current.get(k), new.get(k)] for k in new if current.get(k) != new.get(k)}
    stmt = insert(Setting).values(key=key, value=new, updated_by=user_id, updated_at=utcnow())
    stmt = stmt.on_conflict_do_update(index_elements=[Setting.key],
                                      set_={"value": new, "updated_by": user_id, "updated_at": utcnow()})
    await db.execute(stmt)
    await db.commit()
    invalidate()
    from ..rds import redis
    await redis().publish("settings:invalidate", key)
    return diff
