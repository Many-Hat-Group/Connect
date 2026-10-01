"""Customer instructions, hard-coded in app/content/instructions.yaml,
keyed by share type, OS and browser, with fallbacks."""
from __future__ import annotations

from functools import lru_cache

import yaml

from ..config import APP_DIR

FILE = APP_DIR / "content" / "instructions.yaml"


@lru_cache
def data() -> dict:
    with open(FILE) as f:
        return yaml.safe_load(f)


def lookup(share_type: str, os_key: str, browser: str) -> dict:
    d = data()
    share = d.get(share_type) or d["screen"]
    os_block = share.get(os_key) or share.get("default") or {}
    entry = os_block.get(browser) or os_block.get("default")
    if entry is None:
        entry = share["default"]["default"]
    return entry


def all_keys() -> list[tuple[str, str, str]]:
    out = []
    for share, oses in data().items():
        for os_key, browsers in oses.items():
            for browser in browsers:
                out.append((share, os_key, browser))
    return out


def for_client(share_type: str) -> dict:
    """The whole block for one share type, for the customer page to pick from
    after refining detection in the browser."""
    return data().get(share_type, {})
