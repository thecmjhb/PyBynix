"""Market session lifecycle: creation, caching, expiry, keyring and sealed payloads."""

import asyncio
import base64
import dataclasses
import time

import pytest

from pybynix import crypto
from pybynix.errors import APIError, ProtocolError
from pybynix.market import MarketLimits, MarketSessionManager

KEY = b"k" * 32
STREAM_KEY = b"s" * 32
NEXT_STREAM_KEY = b"n" * 32


def b64(value):
    return base64.b64encode(value).decode()


def seal(key, plaintext, nonce=b"\x00" * 12):
    ciphertext = crypto._chacha20_xor(key, 1, nonce, plaintext)
    tag = crypto._poly1305(
        crypto._seal_mac_data(b"", ciphertext), crypto._chacha20_block(key, 0, nonce)[:32]
    )
    return nonce + ciphertext + tag


def session_payload(*, ttl=3600, guest=False, stream=True):
    body = {
        "sid": "sid-1",
        "key": b64(KEY),
        "ttl": ttl,
        "guest": guest,
        "limits": {"history_1m": 150, "symbols_1h": 60, "subs_1m": 60, "candles": 1000},
    }
    if stream:
        body["stream"] = {
            "e": b64(seal(KEY, b'{"id": 7, "key": "%s", "next_id": 8, "next_key": "%s", '
                           b'"rotate_at": %d, "epoch_s": 600}'
                           % (b64(STREAM_KEY).encode(), b64(NEXT_STREAM_KEY).encode(),
                              int(time.time()) + 600)))
        }
    return body


class FakeTransport:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def __call__(self, method, path, *, json=None, params=None, auth=True,
                       allow_anonymous=False):
        self.calls.append((method, path, json))
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        if isinstance(payload, BaseException):
            raise payload
        return payload


async def test_creates_a_session_and_reads_the_limits():
    transport = FakeTransport(session_payload())
    manager = MarketSessionManager(transport)

    session = await manager.ensure()

    assert session.sid == "sid-1"
    assert session.guest is False
    assert session.limits == MarketLimits(150, 60, 60, 1000)
    assert transport.calls[0][1] == "market/session"
    assert transport.calls[0][2]["webdriver"] is False


async def test_the_session_is_reused_until_it_expires():
    transport = FakeTransport(session_payload())
    manager = MarketSessionManager(transport)

    await manager.ensure()
    await manager.ensure()
    await manager.ensure()

    assert len(transport.calls) == 1


async def test_an_expired_session_is_replaced():
    transport = FakeTransport(session_payload(ttl=1), session_payload(ttl=3600))
    manager = MarketSessionManager(transport, ttl_margin=60)

    first = await manager.ensure()
    assert first.is_fresh(margin=60) is False  # 1s ttl is inside the margin
    second = await manager.ensure()

    assert second.sid == "sid-1"
    assert len(transport.calls) == 2


async def test_concurrent_callers_create_one_session():
    transport = FakeTransport(session_payload())
    manager = MarketSessionManager(transport)

    await asyncio.gather(*(manager.ensure() for _ in range(20)))

    assert len(transport.calls) == 1


async def test_a_host_without_the_endpoint_is_reported():
    transport = FakeTransport(APIError(404))
    manager = MarketSessionManager(transport)

    with pytest.raises(ProtocolError):
        await manager.ensure()


async def test_a_short_key_is_rejected():
    bad = session_payload()
    bad["key"] = b64(b"short")
    manager = MarketSessionManager(FakeTransport(bad))

    with pytest.raises(ProtocolError):
        await manager.ensure()


async def test_sealed_payloads_are_opened_and_plaintext_passes_through():
    manager = MarketSessionManager(FakeTransport(session_payload()))
    await manager.ensure()

    sealed = {"e": b64(seal(KEY, b'{"candles": [{"timestamp": 60}]}'))}

    assert manager.open(sealed) == {"candles": [{"timestamp": 60}]}
    assert manager.open({"candles": []}) == {"candles": []}
    assert manager.metrics["decrypted"] == 1


async def test_a_payload_sealed_with_another_key_is_reported():
    manager = MarketSessionManager(FakeTransport(session_payload()))
    await manager.ensure()

    with pytest.raises(ProtocolError):
        manager.open({"e": b64(seal(b"x" * 32, b"{}"))})
    assert manager.metrics["decrypt_errors"] == 1


async def test_the_stream_keyring_serves_the_current_and_next_key():
    manager = MarketSessionManager(FakeTransport(session_payload()))
    session = await manager.ensure()

    assert session.stream_id == 7
    assert session.stream_key_for(7) == STREAM_KEY
    assert session.stream_key_for(8) == NEXT_STREAM_KEY
    assert session.stream_key_for(9) is None


async def test_an_unknown_stream_key_is_not_invented():
    manager = MarketSessionManager(FakeTransport(session_payload()))
    await manager.ensure()

    assert await manager.ensure_stream_key(999) is None


async def test_a_rotated_keyring_is_refreshed_on_demand():
    rotated = session_payload()
    rotated["sid"] = "sid-2"
    transport = FakeTransport(session_payload(), rotated)
    manager = MarketSessionManager(transport)
    await manager.ensure()

    # Pretend the rotation deadline has passed and the id is unknown.
    manager._session = dataclasses.replace(manager._session, rotate_at=time.time() - 1)
    assert await manager.ensure_stream_key(999) is None  # refresh happens, key still absent
    assert len(transport.calls) == 2
    assert manager.current.sid == "sid-2"


async def test_invalidate_forces_a_new_session():
    transport = FakeTransport(session_payload())
    manager = MarketSessionManager(transport)
    await manager.ensure()

    manager.invalidate()
    await manager.ensure()

    assert len(transport.calls) == 2


async def test_a_session_can_be_shared_between_clients():
    """One market session per account: a second one invalidates the first."""
    from pybynix import Bynix

    manager = MarketSessionManager(FakeTransport(session_payload()))
    shared = Bynix.public(market_session=manager)
    other = Bynix.public(market_session=manager)

    assert shared._market is manager and other._market is manager


def test_a_market_session_must_look_like_one():
    from pybynix import Bynix

    with pytest.raises(TypeError):
        Bynix.public(market_session=object())
