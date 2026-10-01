"""Redis pub/sub fan-out to websockets, so the app can run several
processes. Channels: ch:user:<id>, ch:session:<id>, ch:techs, ch:admins,
ch:customer:<session id>."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

from ..rds import redis

log = logging.getLogger("connect.hub")


class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[WebSocket]] = defaultdict(set)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                ps = redis().pubsub()
                await ps.psubscribe("ch:*")
                async for msg in ps.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    await self._deliver(msg["channel"], msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("hub listener failed; reconnecting")
                await asyncio.sleep(1)

    async def _deliver(self, channel: str, data: str) -> None:
        for ws in list(self.subs.get(channel, ())):
            try:
                await ws.send_text(data)
            except Exception:
                self.unsubscribe_all(ws)

    def subscribe(self, ws: WebSocket, *channels: str) -> None:
        for c in channels:
            self.subs[c].add(ws)

    def unsubscribe(self, ws: WebSocket, *channels: str) -> None:
        for c in channels:
            self.subs.get(c, set()).discard(ws)

    def unsubscribe_all(self, ws: WebSocket) -> None:
        for c in list(self.subs):
            self.subs[c].discard(ws)
            if not self.subs[c]:
                del self.subs[c]


hub = Hub()


async def publish(channel: str, message: dict) -> None:
    await redis().publish(channel, json.dumps(message, default=str))
