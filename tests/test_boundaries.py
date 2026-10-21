import asyncio
import hashlib
import json
import os
import subprocess
import sys

import httpx
import pytest

from pybynix import Bynix, Session
from pybynix.errors import AuthenticationError, DuplicateOrderReference, OrderOutcomeUnknown


def test_cli_help_version_and_invalid_count_are_offline():
    for args in [["--help"], ["--version"], ["stream", "--count", "0"]]:
        result = subprocess.run(
            [sys.executable, "-c", "from pybynix.cli import main; main()", *args],
            capture_output=True,
            text=True,
            timeout=10,
            env=dict(os.environ),
        )
        assert result.returncode == (2 if "0" in args else 0)
    assert "1.0.0" in subprocess.check_output(
        [sys.executable, "-c", "from pybynix.cli import main; main()", "--version"], text=True
    )


async def test_public_client_never_authorizes_account_requests():
    client = Bynix.public()
    try:
        with pytest.raises(AuthenticationError):
            await client.get_balance()
        assert client.metrics["requests"] == 0
    finally:
        await client.close()


async def fixture_client(handler):
    client = Bynix(session=Session("test"), max_requests_per_second=None)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    return client


@pytest.mark.parametrize(
    "account,duration,expected", [("demo", 5, True), ("real", 5, False), ("real", 60, True)]
)
async def test_preflight_applies_observed_market_duration_by_account(account, duration, expected):
    posts = []

    def handler(request):
        if request.url.path.endswith("/assets"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "EUR/USD",
                        "trade_symbol": "EUR/USD",
                        "market": "real",
                        "is_active": True,
                    }
                ],
            )
        if request.url.path.endswith("trading-rules"):
            return httpx.Response(200, json={"enabled": True, "min_expiry_real_market_s": 60})
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"trade_id": "accepted"})
        return httpx.Response(200, json={"price": 1.2})

    client = await fixture_client(handler)
    client.allow_real_trading = True
    try:
        if expected:
            await client.place_order("EUR/USD", "call", 1, duration, account_type=account)
            assert posts[0]["account_type"] == account
        else:
            with pytest.raises(ValueError):
                await client.place_order("EUR/USD", "call", 1, duration, account_type=account)
            assert not posts
    finally:
        await client.close()


async def test_duplicate_reference_prevents_second_post():
    writes = []

    def handler(request):
        if request.method == "POST":
            writes.append(request)
            return httpx.Response(200, json={"trade_id": "one"})
        return httpx.Response(200, json={"price": 1})

    client = await fixture_client(handler)
    client.validate_orders = False
    try:
        await client.place_order("EUR/USD OTC", "call", 1, 5, client_ref="same")
        with pytest.raises(DuplicateOrderReference):
            await client.place_order("EUR/USD OTC", "call", 1, 5, client_ref="same")
        assert len(writes) == 1
    finally:
        await client.close()


async def test_cancelled_submission_is_journalled_unknown_without_retry():
    def handler(request):
        if request.method == "POST":
            raise asyncio.CancelledError
        return httpx.Response(200, json={"price": 1})

    client = await fixture_client(handler)
    client.validate_orders = False
    try:
        with pytest.raises(OrderOutcomeUnknown):
            await client.place_order("EUR/USD OTC", "call", 1, 5, client_ref="cancelled")
        rows = await client.unresolved_orders()
        assert rows[0]["state"] == "unknown"
        assert len(rows) == 1
    finally:
        await client.close()


async def test_older_profile_response_cannot_replace_newer_token():
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        waiting.set()
        await release.wait()
        return httpx.Response(
            200, json={"user_id": "owner", "refreshed_token": "old-response-token"}
        )

    client = await fixture_client(handler)
    try:
        task = asyncio.create_task(client.get_profile())
        await waiting.wait()
        client._session = Session("newer-token")
        release.set()
        await task
        assert client.session.token == "newer-token"
        assert client._identity == hashlib.sha256(b"owner").hexdigest()
    finally:
        await client.close()
