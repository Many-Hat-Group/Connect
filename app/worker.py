"""Background worker: sweeper (timeouts), retention and RustDesk sync.

Runs in the same image as the app: `python -m app.worker`.
`python -m app.worker --health` exits 0 if the loop has ticked recently.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable

from . import db, rds
from .config import get_config

log = logging.getLogger("connect.worker")
HEARTBEAT_KEY = "worker:heartbeat"


class Job:
    def __init__(self, name: str, interval: float, fn: Callable[[], Awaitable[None]], lock_ttl: int = 600):
        self.name, self.interval, self.fn, self.lock_ttl = name, interval, fn, lock_ttl
        self.next_run = 0.0


def jobs() -> list[Job]:
    # Imported lazily so tests can import the worker module cheaply.
    from .services import jobs as j
    cfg = get_config()
    return [
        Job("sweep", cfg.worker_sweep_seconds, j.sweep, lock_ttl=60),
        Job("retention", 24 * 3600, j.retention),
        Job("rustdesk_sync", 15 * 60, j.rustdesk_sync_if_due, lock_ttl=1800),
    ]


async def run_job(job: Job) -> None:
    r = rds.redis()
    # A Redis lock keeps the job single-instance if more workers run.
    if not await r.set(f"worker:lock:{job.name}", "1", nx=True, ex=job.lock_ttl):
        return
    try:
        await job.fn()
    except Exception:
        log.exception("job %s failed", job.name)
    finally:
        await r.delete(f"worker:lock:{job.name}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    registered = jobs()
    log.info("worker started: %s", ", ".join(j.name for j in registered))
    try:
        while True:
            now = time.monotonic()
            await rds.redis().set(HEARTBEAT_KEY, str(int(time.time())), ex=300)
            for job in registered:
                if now >= job.next_run:
                    job.next_run = now + job.interval
                    await run_job(job)
            await asyncio.sleep(1)
    finally:
        await rds.close()
        await db.dispose()


async def health() -> int:
    try:
        v = await rds.redis().get(HEARTBEAT_KEY)
        return 0 if v and time.time() - int(v) < 120 else 1
    finally:
        await rds.close()


if __name__ == "__main__":
    if "--health" in sys.argv:
        sys.exit(asyncio.run(health()))
    asyncio.run(main())
