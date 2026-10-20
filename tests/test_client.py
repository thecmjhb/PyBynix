import asyncio
from decimal import Decimal

import httpx
import pytest

from pybynix import AccountType, AuthChain, Bynix, Session
from pybynix.errors import (
    APIError,
    AuthenticationError,
    ChallengeRequired,
    OrderOutcomeUnknown,
    StreamOverflow,
)
from pybynix.stream import Subscription


async def mocked_client(handler, **kwargs):
    client = Bynix(session=Session("test-secret"), **kwargs)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    return client


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "evil.example"},
        {"host": "https://bynixbrokers.com@evil.example"},
        {"session": Session("secret", "bynix.app")},
        {"queue_size": 0},
    ],
)
def test_rejects_unsafe_configuration(kwargs):
    with pytest.raises(ValueError):
        Bynix(**kwargs)


def test_session_repr_does_not_leak_secret():
    assert "do-not-print" not in repr(Session("do-not-print"))


async def test_close_wakes_waiting_stream():
    async def remove(sub):
        pass

    sub = Subscription(remove)
    waiter = asyncio.create_task(anext(sub))
    await asyncio.sleep(0)
    sub.finish()
    with pytest.raises(StopAsyncIteration):
        await waiter


async def test_overflow_is_visible():
    async def remove(sub):
        pass

    sub = Subscription(remove, size=1)
    sub.put(1)
    sub.put(2)
    with pytest.raises(StreamOverflow):
        await anext(sub)
    assert sub.dropped == 1


async def test_concurrent_expiry_refreshes_once():
    refreshes = 0

    async def provider(host):
        nonlocal refreshes
        refreshes += 1
        await asyncio.sleep(0.01)
        return Session("new-token", host)

    def handler(request):
        if request.headers.get("Authorization") == "Bearer test-secret":
            return httpx.Response(401)
        return httpx.Response(200, json={"demo_balance": "100.12"})

    client = await mocked_client(handler, provider=provider)
    try:
        balances = await asyncio.gather(*(client.get_balance() for _ in range(8)))
        assert balances == [Decimal("100.12")] * 8
        assert refreshes == 1
    finally:
        await client.close()


async def test_unknown_order_not_retried():
    writes = 0

    def handler(request):
        nonlocal writes
        if request.method == "GET":
            return httpx.Response(200, json={"price": 1.2345})
        writes += 1
        raise httpx.ReadTimeout("secret upstream message")

    client = await mocked_client(handler)
    try:
        with pytest.raises(OrderOutcomeUnknown) as exc:
            await client.place_order("EUR/USD OTC", "call", 1, 5, client_ref="my-order")
        assert exc.value.client_ref == "my-order"
        assert writes == 1
        assert "secret" not in str(exc.value)
    finally:
        await client.close()


async def test_validation_never_sends_real_order():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("No request expected")

    client = await mocked_client(handler)
    try:
        with pytest.raises(ValueError):
            await client.place_order("EUR/USD OTC", "call", 1, 5, account_type="real")
        for money in ["0", "-1", "NaN", "Infinity", "1.001"]:
            with pytest.raises(ValueError):
                await client.place_order("EUR/USD OTC", "call", money, 5)
        assert calls == []
    finally:
        await client.close()


async def test_non_idempotent_rejection_not_retried_or_refreshed():
    writes = 0

    async def provider(host):
        raise AssertionError("Must not silently retry a POST")

    def handler(request):
        nonlocal writes
        if request.method == "GET":
            return httpx.Response(200, json={"price": 1.2})
        writes += 1
        return httpx.Response(401)

    client = await mocked_client(handler, provider=provider)
    try:
        with pytest.raises(AuthenticationError):
            await client.place_order("EUR/USD OTC", "call", 1, 5)
        assert writes == 1
    finally:
        await client.close()


async def test_candles_sorted_deduplicated_and_exclusive_before():
    def candle(t):
        return {"timestamp": t, "open": 1, "high": 2, "low": 0, "close": 1.1, "volume": 3}

    def handler(request):
        return httpx.Response(
            200, json={"candles": [candle(120), candle(60), candle(60), candle(180)]}
        )

    client = await mocked_client(handler)
    try:
        result = await client.get_candles("EUR/USD OTC", before=180)
        assert [c.timestamp for c in result] == [60, 120]
        assert result[0].close == Decimal("1.1")
    finally:
        await client.close()


async def test_isolated_account_defaults():
    first, second = Bynix(), Bynix()
    try:
        await first.change_account("real")
        assert first.account_type is AccountType.REAL
        assert second.account_type is AccountType.DEMO
        assert first._streams is not second._streams
    finally:
        await first.close()
        await second.close()


async def test_auth_chain_only_falls_back_on_challenge():
    calls = []

    async def challenge(host):
        calls.append("challenge")
        raise ChallengeRequired()

    async def success(host):
        calls.append("success")
        return Session("token", host)

    async def invalid(host):
        raise AuthenticationError(401)

    session = await AuthChain(challenge, success)("bynixbrokers.com")
    assert session.host == "bynixbrokers.com"
    assert calls == ["challenge", "success"]
    with pytest.raises(AuthenticationError):
        await AuthChain(invalid, success)("bynixbrokers.com")


async def test_error_does_not_echo_body():
    def handler(request):
        return httpx.Response(403, json={"detail": "do-not-expose-token"})

    client = await mocked_client(handler)
    try:
        with pytest.raises(APIError) as error:
            await client.get_balance()
        assert "do-not-expose" not in str(error.value)
    finally:
        await client.close()
