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
    client = Bynix(session=Session("test-secret"), validate_orders=False, **kwargs)
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


async def test_buy_and_wait_places_once_and_waits_for_settlement():
    client = Bynix(session=Session("test-secret"), validate_orders=False)
    calls = []

    async def place(*args, **kwargs):
        calls.append(("place", args, kwargs))
        return {"trade_id": "trade-1", "client_ref": "ref-1"}

    async def wait(*args, **kwargs):
        calls.append(("wait", args, kwargs))
        return {"trade_id": "trade-1", "status": "won"}

    client.place_order = place
    client.wait_order_result = wait
    try:
        result = await client.buy_and_wait(
            "1.00",
            "EUR/USD OTC",
            "call",
            5,
            account_type="demo",
            client_ref="ref-1",
            timeout=30,
            poll_interval=1,
        )
        assert result == {
            "order": {"trade_id": "trade-1", "client_ref": "ref-1"},
            "result": {"trade_id": "trade-1", "status": "won"},
        }
        assert [call[0] for call in calls] == ["place", "wait"]
        assert calls[0][1] == ("EUR/USD OTC", "call", "1.00", 5)
        assert calls[1][1] == ("trade-1",)
    finally:
        await client.close()


async def test_sell_delegates_to_existing_early_close():
    client = Bynix(session=Session("test-secret"), validate_orders=False)
    calls = []

    async def close(trade_id):
        calls.append(trade_id)
        return {"trade_id": trade_id, "status": "closed"}

    client.close_trade_early = close
    try:
        assert await client.sell("trade-2") == {"trade_id": "trade-2", "status": "closed"}
        assert calls == ["trade-2"]
    finally:
        await client.close()


async def test_candle_batch_preserves_order_and_bounds_concurrency():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        symbol = request.url.params["symbol"]
        active -= 1
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "timestamp": len(symbol),
                        "open": 1,
                        "high": 2,
                        "low": 1,
                        "close": 1.5,
                        "volume": 3,
                    }
                ]
            },
        )

    client = await mocked_client(handler)
    try:
        result = await client.get_candles_batch(
            [{"symbol": "x" * length, "count": 1} for length in range(1, 7)],
            max_concurrency=2,
        )
        assert [candles[0].timestamp for candles in result] == [1, 2, 3, 4, 5, 6]
        assert peak <= 2
    finally:
        await client.close()


async def test_historical_candle_cache_is_opt_in_and_never_caches_latest_query():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "candles": [
                    {"timestamp": 90, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 3}
                ]
            },
        )

    client = await mocked_client(handler)
    try:
        await client.get_candles("EUR/USD OTC", before=100, cache_ttl=60)
        await client.get_candles("EUR/USD OTC", before=100, cache_ttl=60)
        await client.get_candles("EUR/USD OTC", cache_ttl=60)
        await client.get_candles("EUR/USD OTC", cache_ttl=60)
        assert calls == 3
    finally:
        await client.close()


async def test_candle_batch_rejects_unsafe_or_ambiguous_requests_before_network():
    def handler(request):
        raise AssertionError("validation must happen before network access")

    client = await mocked_client(handler)
    try:
        with pytest.raises(ValueError):
            await client.get_candles_batch([{"symbol": "EUR/USD OTC", "endpoint": "other"}])
        with pytest.raises(ValueError):
            await client.get_candles_batch([{"symbol": "EUR/USD OTC"}], max_concurrency=0)
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


async def test_slow_profile_health_refresh_does_not_block_market_polling():
    profile_started = asyncio.Event()
    profile_release = asyncio.Event()
    profile_calls = 0
    tick = {
        "symbol": "EUR/USD OTC",
        "interval": "1m",
        "price": 1.2,
        "timestamp": 100000,
        "server_time": 100000,
        "candle": None,
        "closed": None,
    }

    async def handler(request):
        nonlocal profile_calls
        if request.url.path.endswith("/auth/me"):
            profile_calls += 1
            if profile_calls > 1:
                profile_started.set()
                await profile_release.wait()
            return httpx.Response(200, json={"user_id": "owner"})
        return httpx.Response(200, json=tick)

    client = await mocked_client(
        handler,
        stream_transport="polling",
        poll_interval=0.5,
        max_requests_per_second=None,
    )
    client.profile_refresh_interval = 0.01
    client._profile_poll_timeout = 0.05
    client._market_poll_timeout = 0.05
    try:
        await client.connect()
        async with await client.subscribe_ticks("EUR/USD OTC") as sub:
            await asyncio.wait_for(profile_started.wait(), 1)
            received = await asyncio.wait_for(anext(sub), 1.5)
            assert received.price == Decimal("1.2")
            assert client.metrics["poll_fallbacks"] >= 1
            assert profile_calls >= 2
    finally:
        profile_release.set()
        await client.close()


async def test_supervision_polling_uses_zero_retry_budget():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(503)

    client = await mocked_client(handler, max_requests_per_second=None)
    client._profile_poll_timeout = 0.05
    client._last_profile_refresh = 0
    try:
        await client._poll_one(("EUR/USD OTC", "1m"))
        assert len(calls) == 1
        assert client.metrics["read_retries"] == 0

        profile_task = asyncio.create_task(client._supervise_profile())
        await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        profile_task.cancel()
        await profile_task
        assert len(calls) == 2
        assert client.metrics["read_retries"] == 0
    finally:
        await client.close()
