"""Redis pub/sub fan-out: a message published by any process reaches the
websockets subscribed in another process (here: a separate Hub instance)."""
import asyncio
import json

from app.ws.hub import Hub, publish


class FakeWS:
    def __init__(self):
        self.got = []

    async def send_text(self, t):
        self.got.append(json.loads(t))


async def test_publish_reaches_subscribers_on_other_hub():
    a, b = Hub(), Hub()
    a.start(); b.start()
    try:
        w1, w2, w3 = FakeWS(), FakeWS(), FakeWS()
        a.subscribe(w1, "ch:user:1")
        b.subscribe(w2, "ch:user:1", "ch:techs")
        b.subscribe(w3, "ch:user:2")
        await asyncio.sleep(0.3)  # let both pattern subscriptions register
        await publish("ch:user:1", {"type": "x", "n": 1})
        await publish("ch:techs", {"type": "y"})
        for _ in range(50):
            if w1.got and len(w2.got) == 2:
                break
            await asyncio.sleep(0.05)
        assert w1.got == [{"type": "x", "n": 1}]
        assert [m["type"] for m in w2.got] == ["x", "y"]
        assert w3.got == []
        b.unsubscribe_all(w2)
        await publish("ch:user:1", {"type": "z"})
        await asyncio.sleep(0.3)
        assert [m["type"] for m in w2.got] == ["x", "y"]
    finally:
        await a.stop(); await b.stop()
