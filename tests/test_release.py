import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from pybynix import (
    Bynix,
    Candle,
    ClientPool,
    MemoryJournal,
    PasswordAuth,
    Session,
    SQLiteJournal,
    export_candles,
    load_session,
    resample_candles,
    save_session,
)
from pybynix.errors import AuthenticationError, DuplicateOrderReference, ProtocolError
from pybynix.sentiment import SentimentTracker


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_journal_isolates_owners_resists_duplicate_and_recovers(kind, tmp_path):
    journal = MemoryJournal() if kind == "memory" else SQLiteJournal(tmp_path / "orders.db")
    await journal.reserve("owner-a", "one", {"account_type": "demo"})
    await journal.reserve("owner-b", "one", {"account_type": "demo"})
    with pytest.raises(DuplicateOrderReference):
        await journal.reserve("owner-a", "one", {})
    await journal.update("owner-a", "one", trade_id="server-id", state="accepted")
    if kind == "sqlite":
        journal = SQLiteJournal(tmp_path / "orders.db")
    row = await journal.get("owner-a", "one")
    assert row["trade_id"] == "server-id"
    assert (await journal.get("owner-b", "one"))["trade_id"] is None
    await journal.update_trade("owner-a", "server-id", "settled")
    assert await journal.unresolved("owner-a") == []
    assert len(await journal.unresolved("owner-b")) == 1


async def test_concurrent_sqlite_reservation_only_one_wins(tmp_path):
    first = SQLiteJournal(tmp_path / "orders.db")
    second = SQLiteJournal(tmp_path / "orders.db")
    result = await asyncio.gather(
        first.reserve("owner", "same", {}),
        second.reserve("owner", "same", {}),
        return_exceptions=True,
    )
    assert sum(isinstance(x, DuplicateOrderReference) for x in result) == 1


async def test_memory_journal_never_evicts_unknown_intent():
    journal = MemoryJournal(capacity=1)
    await journal.reserve("owner", "first", {})
    await journal.update("owner", "first", state="unknown")
    with pytest.raises(ValueError):
        await journal.reserve("owner", "next", {})
    assert (await journal.get("owner", "first"))["state"] == "unknown"
    await journal.update("owner", "first", state="rejected")
    await journal.reserve("owner", "next", {})


def test_atomic_session_roundtrip_and_repr(tmp_path):
    path = tmp_path / "nested" / "session.json"
    save_session(Session("one"), path)
    save_session(Session("two"), path)
    assert load_session(path).token == "two"
    assert list(path.parent.glob(".pybynix-session-*")) == []
    with pytest.raises(ValueError):
        Session("invalid\r\nheader")


@pytest.mark.parametrize("money", [True, "text", "1e1000", "sNaN", None])
async def test_invalid_money_never_queries_network(money):
    client = Bynix()
    try:
        with pytest.raises(ValueError):
            await client.place_order("EUR/USD OTC", "call", money, 5)
        assert client.metrics["requests"] == 0
    finally:
        await client.close()


def test_resampling_preserves_ohlc_and_refuses_gaps(tmp_path):
    def candle(t, close):
        return Candle(t, Decimal(1), Decimal(3), Decimal("0.5"), Decimal(close), Decimal(2))

    values = [candle(0, "1.2"), candle(60, "1.5"), candle(120, "1.1")]
    combined = resample_candles(values, source_seconds=60, target_seconds=180)
    assert len(combined) == 1 and combined[0].volume == 6
    assert combined[0].close == Decimal("1.1")
    assert resample_candles(values[1:], source_seconds=60, target_seconds=180) == []
    export_candles(combined, tmp_path / "data.json")
    export_candles(combined, tmp_path / "data.csv")
    assert json.loads((tmp_path / "data.json").read_text())[0]["close"] == "1.1"
    assert "timestamp,open,high,low,close,volume" in (tmp_path / "data.csv").read_text()


def test_sentiment_follows_observed_weights_and_stays_bounded():
    tracker = SentimentTracker()
    tracker.set_candles([Candle(0, Decimal(1), Decimal(2), Decimal(1), Decimal(2), Decimal(0))])
    assert tracker.snapshot()["up"] == 83  # JS round(65 * 1 + 35 * .5)
    for price in range(1, 200):
        tracker.update(price)
    assert tracker.snapshot()["up"] == 95
    assert tracker.snapshot()["is_trader_positioning"] is False


async def test_http_read_retries_but_does_not_retry_auth_failure():
    codes = [503, 200]
    client = Bynix(session=Session("test"), max_requests_per_second=None)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(codes.pop(0), json={"demo_balance": 5})
        ),
    )
    try:
        assert await client.get_balance() == 5
        assert client.metrics["read_retries"] == 1
    finally:
        await client.close()


async def test_refresh_cannot_switch_to_a_different_owner():
    async def provider(host):
        return Session("new", host)

    client = Bynix(session=Session("old"), provider=provider, max_requests_per_second=None)
    await client._http.aclose()

    def handler(request):
        owner = "owner-b" if request.headers["Authorization"] == "Bearer new" else "owner-a"
        return httpx.Response(200, json={"user_id": owner, "demo_balance": 1})

    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    try:
        await client.get_profile()
        with pytest.raises(AuthenticationError):
            await client._authenticate("old")
        assert client.session.token == "old"
    finally:
        await client.close()


async def test_otp_callback_uses_observed_verify_endpoint(monkeypatch):
    original = httpx.AsyncClient
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/2fa"):
            assert json.loads(request.content)["code"] == "123456"
            return httpx.Response(200, json={"access_token": "verified"})
        return httpx.Response(
            200, json={"requires_2fa": True, "challenge_id": "private-id", "method": "email"}
        )

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("pybynix.auth.httpx.AsyncClient", factory)

    async def otp(challenge):
        assert "private-id" not in repr(challenge)
        return "123456"

    session = await PasswordAuth("example@example.com", "dummy", otp_callback=otp)(
        "bynixbrokers.com"
    )
    assert session.token == "verified"
    assert paths == ["/api/auth/login", "/api/auth/login/2fa"]


async def test_polling_mode_needs_no_socket_namespace():
    tick = {
        "symbol": "EUR/USD OTC",
        "interval": "1m",
        "price": 1.2,
        "timestamp": 100000,
        "server_time": 100000,
        "candle": None,
        "closed": None,
    }
    client = Bynix(
        session=Session("test"),
        stream_transport="polling",
        poll_interval=0.5,
        max_requests_per_second=None,
    )
    await client._http.aclose()

    def handler(request):
        return httpx.Response(
            200, json=tick if "synthetic" in request.url.path else {"user_id": "owner"}
        )

    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    try:
        await client.connect()
        assert not client._socket.connected
        async with await client.subscribe_ticks("EUR/USD OTC") as sub:
            result = await asyncio.wait_for(anext(sub), 2)
            assert result.price == Decimal("1.2")
        assert client.metrics["poll_fallbacks"] >= 1
    finally:
        await client.close()


async def test_pool_closes_every_client_on_partial_startup_failure():
    closed = []

    class Client:
        def __init__(self, name):
            self.name = name

        async def connect(self):
            if self.name == "bad":
                raise ProtocolError("failed")

        async def close(self):
            closed.append(self.name)

    with pytest.raises(ProtocolError):
        await ClientPool({"a": Client("ok"), "b": Client("bad")}).connect()
    assert sorted(closed) == ["bad", "ok"]
