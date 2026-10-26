import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest

from pybynix import Bynix, FileSessionStore, RequestPacer, Session, load_session, save_session
from pybynix.errors import (
    APIError,
    AuthenticationError,
    ChallengeRequired,
    ProtocolError,
    RateLimitError,
)
from pybynix.session import BynixSessionManager, token_expiry

HOST = "bynixbrokers.com"
NOW = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def make_token(payload):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature-not-checked"


def provider_for(clock, lifetime=3600, delay=0.0):
    calls = []

    async def provider(host):
        calls.append(host)
        if delay:
            await asyncio.sleep(delay)
        return Session(make_token({"exp": clock.now + lifetime + len(calls)}), host)

    provider.calls = calls
    return provider


async def mocked_client(handler, **kwargs):
    client = Bynix(
        session=Session(make_token({"exp": NOW + 3600}), HOST),
        max_requests_per_second=None,
        validate_orders=False,
        **kwargs,
    )
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url=f"https://{HOST}/api/", transport=httpx.MockTransport(handler)
    )
    return client


def test_token_expiry_reads_exp_without_trusting_signature():
    assert token_expiry(make_token({"exp": NOW + 60})) == NOW + 60
    assert token_expiry("opaque-token") is None
    assert token_expiry("a.%%%.c") is None
    assert token_expiry(make_token({"exp": "soon"})) is None
    assert token_expiry(make_token({"exp": True})) is None
    assert token_expiry(make_token({})) is None


def test_manager_rejects_unsafe_or_invalid_configuration():
    provider = provider_for(Clock())
    with pytest.raises(ValueError):
        BynixSessionManager("https://bynixbrokers.com@evil.example", provider)
    with pytest.raises(TypeError):
        BynixSessionManager(HOST, "not-callable")
    with pytest.raises(ValueError):
        BynixSessionManager(HOST, provider, refresh_margin=-1)
    with pytest.raises(ValueError):
        BynixSessionManager(HOST, provider, coalesce_seconds=float("nan"))
    with pytest.raises(ValueError):
        BynixSessionManager(HOST, provider, coalesce_seconds=10, max_backoff=5)


def test_manager_repr_never_prints_the_token():
    clock = Clock()
    manager = BynixSessionManager(HOST, provider_for(clock), clock=clock)
    token = make_token({"exp": NOW + 3600})
    manager.adopt(Session(token, HOST))
    assert token not in repr(manager)
    assert "loaded" in repr(manager)


async def test_due_token_is_renewed_once_for_concurrent_callers():
    clock = Clock()
    provider = provider_for(clock, delay=0.01)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    manager.adopt(Session(make_token({"exp": NOW + 60}), HOST))
    clock.now += 11  # Past the coalesce window, and inside the refresh margin.

    sessions = await asyncio.gather(*(manager.get_session() for _ in range(8)))

    assert len(provider.calls) == 1
    assert {s.token for s in sessions} == {manager.session.token}


async def test_fresh_token_is_not_renewed():
    clock = Clock()
    provider = provider_for(clock)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    manager.adopt(Session(make_token({"exp": NOW + 3600}), HOST))
    clock.now += 11
    await manager.get_session()
    assert provider.calls == []


async def test_stored_session_is_used_without_login(tmp_path):
    clock = Clock()
    path = tmp_path / "session.json"
    token = make_token({"exp": NOW + 3600})
    save_session(Session(token, HOST), path)
    provider = provider_for(clock)
    manager = BynixSessionManager(HOST, provider, store=FileSessionStore(path, HOST), clock=clock)

    assert (await manager.get_session()).token == token
    assert provider.calls == []


async def test_expired_stored_session_renews_and_persists(tmp_path):
    clock = Clock()
    path = tmp_path / "session.json"
    save_session(Session(make_token({"exp": NOW - 10}), HOST), path)
    provider = provider_for(clock)
    manager = BynixSessionManager(HOST, provider, store=FileSessionStore(path, HOST), clock=clock)

    session = await manager.get_session()

    assert len(provider.calls) == 1
    assert load_session(path).token == session.token


def test_store_ignores_session_from_another_host_or_corrupt_file(tmp_path):
    path = tmp_path / "session.json"
    save_session(Session(make_token({"exp": NOW + 1}), "bynix.app"), path)
    assert FileSessionStore(path, HOST).load() is None
    path.write_text("not json", encoding="utf-8")
    assert FileSessionStore(path, HOST).load() is None
    assert FileSessionStore(tmp_path / "missing.json", HOST).load() is None


async def test_refresh_with_already_rotated_token_reuses_new_session():
    clock = Clock()
    provider = provider_for(clock)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    manager.adopt(Session(make_token({"exp": NOW + 3600}), HOST))
    clock.now += 11
    stale = manager.session.token
    fresh = await manager.refresh(stale)
    assert len(provider.calls) == 1

    assert await manager.refresh(stale) is fresh
    assert len(provider.calls) == 1


async def test_forced_refresh_inside_coalesce_window_reuses_login():
    clock = Clock()
    provider = provider_for(clock)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    manager.adopt(Session(make_token({"exp": NOW + 3600}), HOST))

    await manager(HOST)
    assert provider.calls == []

    clock.now += 11
    await manager(HOST)
    assert len(provider.calls) == 1


async def test_authentication_denial_drops_the_stale_token():
    clock = Clock()

    async def denied(host):
        raise AuthenticationError(401)

    manager = BynixSessionManager(HOST, denied, clock=clock)
    token = make_token({"exp": NOW + 3600})
    manager.adopt(Session(token, HOST))
    clock.now += 11
    with pytest.raises(AuthenticationError):
        await manager.refresh(token)
    assert manager.session is None


async def test_transient_failure_keeps_a_still_usable_token():
    clock = Clock()

    async def unreachable(host):
        raise ProtocolError("Login transport failed")

    manager = BynixSessionManager(HOST, unreachable, clock=clock)
    token = make_token({"exp": NOW + 3600})
    manager.adopt(Session(token, HOST))
    clock.now += 11
    with pytest.raises(ProtocolError):
        await manager.refresh(token)
    assert manager.session.token == token


async def test_provider_for_another_host_is_rejected():
    clock = Clock()

    async def wrong_host(host):
        return Session(make_token({"exp": NOW + 3600}), "bynix.app")

    manager = BynixSessionManager(HOST, wrong_host, clock=clock)
    with pytest.raises(ValueError):
        await manager.refresh()
    assert manager.session is None
    with pytest.raises(ValueError):
        await manager("bynix.app")


async def test_challenge_stops_background_renewal():
    async def challenged(host):
        raise ChallengeRequired("sms")

    manager = BynixSessionManager(HOST, challenged, clock=Clock())
    with pytest.raises(ChallengeRequired):
        await asyncio.wait_for(manager.run(), 1)


async def test_run_backs_off_on_transient_failures_and_honours_retry_after(monkeypatch):
    class StopRun(Exception):
        pass

    clock = Clock()
    delays = []
    outcomes = [ProtocolError("temporary"), RateLimitError("45"), "ok"]

    async def fake_sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise StopRun

    async def provider(host):
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return Session(make_token({"exp": clock.now + 3600}), host)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    with pytest.raises(StopRun):
        await manager.run()
    # Backoff 10s, then Retry-After 45s, then renewal 300s before the 3600s expiry.
    assert delays == [10, 45, 3300]


async def test_manager_drives_client_401_renewal_exactly_once():
    clock = Clock()
    provider = provider_for(clock, delay=0.01)
    manager = BynixSessionManager(HOST, provider, clock=clock)
    old = make_token({"exp": NOW + 3600})

    def handler(request):
        if request.headers.get("Authorization") == f"Bearer {old}":
            return httpx.Response(401)
        return httpx.Response(200, json={"demo_balance": "5.50"})

    client = await mocked_client(handler, provider=manager)
    try:
        balances = await asyncio.gather(*(client.get_balance() for _ in range(8)))
        assert balances == [Decimal("5.50")] * 8
        assert len(provider.calls) == 1
    finally:
        await client.close()


async def test_403_renewal_is_opt_in_and_never_for_challenges():
    logins = []
    old = make_token({"exp": NOW + 3600})

    async def provider(host):
        logins.append(host)
        return Session(make_token({"exp": NOW + 7200}), host)

    def plain(request):
        if request.headers.get("Authorization") == f"Bearer {old}":
            return httpx.Response(403)
        return httpx.Response(200, json={"demo_balance": "1"})

    def challenge(request):
        return httpx.Response(403, json={"code": "challenge_required"})

    default = await mocked_client(plain, provider=provider)
    opted_in = await mocked_client(plain, provider=provider, refresh_on_status=(401, 403))
    guarded = await mocked_client(challenge, provider=provider, refresh_on_status=(401, 403))
    try:
        with pytest.raises(APIError) as error:
            await default.get_balance()
        assert error.value.status == 403 and logins == []

        assert await opted_in.get_balance() == Decimal("1")
        assert len(logins) == 1

        with pytest.raises(APIError) as error:
            await guarded.get_balance()
        assert error.value.status == 403 and len(logins) == 1
    finally:
        for client in (default, opted_in, guarded):
            await client.close()


def test_client_validates_refresh_statuses_and_pacer():
    session = Session("token")
    with pytest.raises(ValueError):
        Bynix(session=session, refresh_on_status=(500,))
    with pytest.raises(ValueError):
        Bynix(session=session, refresh_on_status=())
    with pytest.raises(TypeError):
        Bynix(session=session, request_pacer=object())


def test_clients_can_share_one_request_pacer():
    pacer = RequestPacer(5)
    first = Bynix(session=Session("first"), request_pacer=pacer)
    second = Bynix(session=Session("second"), request_pacer=pacer)
    assert first._pacer is second._pacer is pacer
