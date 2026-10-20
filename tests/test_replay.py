import json
from pathlib import Path

import pytest

from pybynix import Bynix
from pybynix.errors import CapabilityError, StreamOverflow


async def test_observed_frame_dispatches_only_to_matching_subscribers():
    tick = json.loads((Path(__file__).parent / "fixtures" / "tick.json").read_text())
    client = Bynix(queue_size=1)
    client._ready.set()

    async def emit(*args):
        pass

    client._socket.emit = emit
    matching = await client.subscribe_ticks(tick["symbol"])
    other = await client.subscribe_ticks("GBP/USD OTC")
    try:
        await client._on_tick(tick)
        received = await anext(matching)
        assert received.symbol == tick["symbol"]
        assert received.timestamp == tick["timestamp"]
        assert received.candle.timestamp * 1000 <= received.timestamp
        assert other._queue.empty()
        await client._on_tick({"invalid": True})
        assert client.metrics["malformed_ticks"] == 1
        await client._on_tick(tick)
        await client._on_tick(tick)
        with pytest.raises(StreamOverflow):
            await anext(matching)
    finally:
        await matching.aclose()
        await other.aclose()
        await client.close()


async def test_reconnect_registers_existing_subscriptions_again_and_reports_gap():
    client = Bynix()
    client._ready.set()
    emitted = []

    async def emit(event, payload):
        emitted.append((event, payload))

    client._socket.emit = emit
    sub = await client.subscribe_ticks("EUR/USD OTC")
    events = client.connection_events()
    try:
        await client._on_disconnect()
        disconnected = await anext(events)
        assert disconnected.kind == "disconnected"
        assert not client.connected
        await client._on_connect()
        restored = await anext(events)
        assert restored.kind == "connected"
        assert "gap" in restored.detail.lower()
        assert [e for e, _ in emitted].count("subscribe_synthetic") == 2
    finally:
        await sub.aclose()
        await events.aclose()
        await client.close()


async def test_upstream_sentiment_never_fabricated():
    client = Bynix()
    try:
        with pytest.raises(CapabilityError):
            await client.get_realtime_sentiment("EUR/USD OTC")
    finally:
        await client.close()


def test_invalid_dns_and_ambiguous_network_settings_rejected():
    with pytest.raises(ValueError):
        Bynix(dns_overrides={"bynixbrokers.com": "not-an-ip"})
    with pytest.raises(ValueError):
        Bynix(proxy="http://localhost:1234", dns_overrides={"bynixbrokers.com": "1.2.3.4"})
    with pytest.raises(ValueError):
        Bynix(proxy="socks5://localhost:1234")
