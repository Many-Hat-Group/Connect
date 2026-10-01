"""Public /licences page."""
from functools import lru_cache

import yaml
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import APP_DIR
from ..db import get_db
from ..services import rustdesk
from ..web import render

router = APIRouter()


@lru_cache
def components() -> list[dict]:
    with open(APP_DIR / "content" / "licences.yaml") as f:
        return yaml.safe_load(f)["components"]


@router.get("/licences")
async def licences(request: Request, db: AsyncSession = Depends(get_db)):
    rel = await rustdesk.active(db)
    items = []
    for c in components():
        c = dict(c)
        if c["version"] == "dynamic":
            c["version"] = rel.version if rel else "not mirrored yet"
            if rel:
                c["source"] = f"https://github.com/rustdesk/rustdesk/tree/{rel.tag}"
        items.append(c)
    return await render(request, db, "licences.html", {"items": items})
