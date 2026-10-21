"""Bynix's observed REST + Socket.IO protocol, independently implemented."""

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from urllib.parse import quote

import aiohttp
import httpx
import socketio

from .auth import SessionProvider
from .errors import (
    APIError,
    AuthenticationError,
    OrderJournalError,
    OrderOutcomeUnknown,
    ProtocolError,
    RateLimitError,
)
from .journal import MemoryJournal
from .models import AccountType, Candle, ConnectionEvent, Session, Tick
from .network import DNSHTTPTransport, StaticResolver, validate_dns, validate_dns_servers
from .runtime import RequestPacer
from .sentiment import SentimentTracker
from .stream import Subscription


def _host(value):
    value = value.removeprefix("https://").rstrip("/").lower()
    if not re.fullmatch(r"[a-z0-9.-]+", value) or ".." in value:
        raise ValueError("Expected an HTTPS hostname, without path or port")
    return value


def _interval(value):
    if not re.fullmatch(r"[1-9][0-9]*[smhd]", value):
        raise ValueError("Interval must be a positive duration such as 5s or 1m")
    return value


class Bynix:
    def __init__(
        self,
        *,
        session: Session | None = None,
        provider: SessionProvider | None = None,
        host="bynixbrokers.com",
        trusted_hosts=(),
        account_type=AccountType.DEMO,
        allow_real_trading=False,
        timeout=20.0,
        proxy=None,
        dns_overrides=None,
        dns_servers=(),
        dns_port=53,
        queue_size=256,
        journal=None,
        stream_transport="auto",
        poll_interval=1.0,
        profile_refresh_interval=60.0,
        read_retries=2,
        validate_orders=True,
        challenge_id=None,
        max_requests_per_second=10,
        max_subscriptions=256,
        anonymous=False,
    ):
        self.host = _host(host)
        known = set(json.loads(files("pybynix").joinpath("domains.json").read_text()))
        known.update(_host(h) for h in trusted_hosts)
        if self.host not in known:
            raise ValueError("Host must be explicitly trusted")
        if session and _host(session.host) != self.host:
            raise ValueError("Session host mismatch; authenticate separately on the new host")
        if anonymous and (session is not None or provider is not None):
            raise ValueError("Anonymous mode cannot also supply an authenticated session/provider")
        self.anonymous = anonymous
        if (
            not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or not isinstance(queue_size, int)
            or isinstance(queue_size, bool)
            or queue_size < 1
        ):
            raise ValueError("Timeout and queue size must be positive")
        if stream_transport not in {"auto", "websocket", "polling"}:
            raise ValueError("stream_transport must be auto, websocket or polling")
        if not math.isfinite(poll_interval) or poll_interval < 0.5:
            raise ValueError("poll_interval must be finite and at least 0.5 seconds")
        if not math.isfinite(profile_refresh_interval) or profile_refresh_interval < 10:
            raise ValueError("profile_refresh_interval must be at least 10 seconds")
        if not isinstance(read_retries, int) or not 0 <= read_retries <= 5:
            raise ValueError("read_retries must be 0..5")
        self._session, self._provider = session, provider
        self.account_type = AccountType(account_type)
        self.allow_real_trading = allow_real_trading
        self.timeout, self.queue_size = timeout, queue_size
        if proxy and (dns_overrides or dns_servers):
            raise ValueError("Use proxy or static DNS overrides, not both")
        if proxy and not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise ValueError("Choose an HTTP(S) CONNECT or SOCKS5 proxy")
        self._proxy = proxy
        self._dns = validate_dns(dns_overrides)
        self._dns_servers = validate_dns_servers(dns_servers)
        self._dns_port = dns_port
        self._is_socks = bool(proxy and proxy.startswith(("socks5://", "socks5h://")))
        if self._is_socks:
            try:
                import aiohttp_socks  # noqa: F401
                import socksio  # noqa: F401
            except ImportError:
                raise ValueError("SOCKS5 requires pip install 'pybynix[network]'") from None
        self._socket_http = None
        self.stream_transport = stream_transport
        self.poll_interval = poll_interval
        self.profile_refresh_interval = profile_refresh_interval
        self.read_retries = read_retries
        self.validate_orders = validate_orders
        self.challenge_id = challenge_id
        self._asset_cache = (0.0, [])
        self._rules_cache = (0.0, {})
        if not isinstance(max_subscriptions, int) or max_subscriptions < 1:
            raise ValueError("max_subscriptions must be positive")
        self.max_subscriptions = max_subscriptions
        self._pacer = RequestPacer(max_requests_per_second)
        self._request_semaphore = asyncio.Semaphore(8)
        self._journal = journal if journal is not None else MemoryJournal()
        self._scope = "unbound"
        self._identity = None
        self._supervisor = None
        self._profile_supervisor = None
        self._last_profile_refresh = 0.0
        self._last_tick = {}
        self._last_tick_key = {}
        self._sentiment = {}
        self._socket_retry_at = 0.0
        self._connect_attempt_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=f"https://{self.host}/api/",
            timeout=timeout,
            follow_redirects=False,
            proxy=proxy,
            transport=DNSHTTPTransport(self._dns, nameservers=self._dns_servers, port=dns_port)
            if self._dns or self._dns_servers
            else None,
            trust_env=False,
        )
        self._profile_poll_timeout = min(float(timeout), 5.0)
        self._market_poll_timeout = min(float(timeout), 5.0)
        self._socket = self._make_socket()
        self._auth_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._closed = False
        self._generation = 0
        self._streams: dict[tuple, set[Subscription]] = {}
        self._latest_prices = {}
        self._price_timestamps = {}
        self._known_orders = OrderedDict()
        self._account_streams: set[Subscription] = set()
        self._connection_streams: set[Subscription] = set()
        self.metrics = {
            "ticks": 0,
            "malformed_ticks": 0,
            "connections": 0,
            "requests": 0,
            "poll_fallbacks": 0,
            "read_retries": 0,
            "duplicate_ticks": 0,
        }

    def _make_socket(self, http_session=None):
        client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=8,
            reconnection_delay=1,
            reconnection_delay_max=20,
            logger=False,
            engineio_logger=False,
            http_session=http_session,
            websocket_extra_options={"proxy": self._proxy}
            if self._proxy and not self._is_socks
            else {},
        )
        client.on("connect", self._on_connect)
        client.on("disconnect", self._on_disconnect)
        client.on("synthetic_tick", self._on_tick)
        client.on("account_event", self._on_account)
        return client

    @classmethod
    async def connect_any(cls, hosts, *, provider, **kwargs):
        """Try trusted hosts at login, authenticating separately on each host.

        A session/order is never silently migrated between hosts. Authentication
        denial and rate limits stop this chain rather than rotating around them.
        """
        for host in hosts:
            client = cls(host=host, provider=provider, **kwargs)
            try:
                return await client.connect()
            except (ProtocolError, OSError, TimeoutError, httpx.TransportError):
                await client.close()
            except BaseException:
                await client.close()
                raise
        raise ProtocolError("No configured host connected")

    @classmethod
    def public(cls, **kwargs):
        """Create a public market-data client without account credentials."""
        return cls(anonymous=True, **kwargs)

    @classmethod
    def from_token(cls, token, *, host="bynixbrokers.com", **kwargs):
        return cls(session=Session(token, host), host=host, **kwargs)

    @classmethod
    def from_session_file(cls, path, **kwargs):
        from .auth import load_session

        session = load_session(path)
        return cls(session=session, host=session.host, **kwargs)

    def set_session(self, *, token=None, ssid=None):
        """Set a Bynix access token before connecting (not a Socket.IO SID)."""
        if self.connected or self._closed or self._identity is not None:
            raise RuntimeError("Set the session before connecting")
        if self.anonymous or bool(token) == bool(ssid):
            raise ValueError("Choose exactly one token or ssid on an authenticated client")
        self._session = Session(token or ssid, self.host)

    @property
    def session(self):
        return self._session

    @property
    def connected(self):
        return self._ready.is_set() and not self._closed

    def _connection_event(self, kind, detail=""):
        event = ConnectionEvent(kind, self._generation, detail)
        for sub in tuple(self._connection_streams):
            sub.put(event)

    async def _on_connect(self):
        self._generation += 1
        self.metrics["connections"] += 1
        for symbol, interval in tuple(self._streams):
            await self._socket.emit("subscribe_synthetic", {"symbol": symbol, "interval": interval})
        if self._session:
            await self._socket.emit("subscribe_account", {"token": self._session.token})
        self._ready.set()
        self._connection_event("connected", "Streams may contain a gap after reconnection")

    async def _on_disconnect(self, *args):
        self._ready.clear()
        self._connection_event("disconnected", "Live data gap; use history to resync")

    async def _on_tick(self, data):
        try:
            tick = Tick.from_wire(data)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            self.metrics["malformed_ticks"] += 1
            return
        self.metrics["ticks"] += 1
        key = (tick.symbol, tick.interval)
        marker = (
            data.get("epoch", 0),
            tick.timestamp,
            tick.price,
            tick.closed.timestamp if tick.closed else None,
            tick.payout,
            tick.candle.volume if tick.candle else None,
        )
        previous = self._last_tick_key.get(key)
        self._last_tick[key] = time.monotonic()
        if previous == marker:
            self.metrics["duplicate_ticks"] += 1
            return
        if previous and previous[0] == marker[0] and tick.timestamp < previous[1]:
            return
        self._last_tick_key[key] = marker
        if tick.timestamp >= self._price_timestamps.get(tick.symbol, 0):
            self._price_timestamps[tick.symbol] = tick.timestamp
            self._latest_prices[tick.symbol] = (tick.price, time.monotonic())
            if tick.symbol in self._sentiment:
                self._sentiment[tick.symbol][0].update(tick.price)
        for sub in tuple(self._streams.get((tick.symbol, tick.interval), ())):
            sub.put(tick)

    async def _on_account(self, data):
        if isinstance(data, dict):
            if data.get("kind") == "trade_closed" and data.get("trade_id"):
                try:
                    await self._journal.update_trade(self._scope, data["trade_id"], "settled")
                except Exception:
                    # A local storage problem must not hide a genuine server event.
                    self._connection_event("journal_error", "Could not persist account event")
            for sub in tuple(self._account_streams):
                sub.put(data)

    async def _authenticate(self, stale_token=None):
        if not self._provider:
            raise AuthenticationError(401)
        async with self._auth_lock:
            if stale_token and self._session and self._session.token != stale_token:
                return
            session = await self._provider(self.host)
            if not isinstance(session, Session) or _host(session.host) != self.host:
                raise ValueError("Auth provider returned a session for a different host")
            if self._identity:
                response = await self._http.get(
                    "auth/me", headers={"Authorization": f"Bearer {session.token}"}
                )
                if not response.is_success:
                    raise AuthenticationError(response.status_code)
                profile = response.json()
                identity = profile.get("user_id") or profile.get("account_id")
                if (
                    not identity
                    or hashlib.sha256(str(identity).encode()).hexdigest() != self._identity
                ):
                    raise AuthenticationError(403, "account_changed")
            self._session = session
            if self._socket.connected:
                await self._socket.emit("subscribe_account", {"token": session.token})

    async def connect(self):
        async with self._connect_lock:
            if self._closed:
                raise RuntimeError("Client is closed; create a new client")
            if self.connected:
                return self
            if not self._session and not self.anonymous:
                await self._authenticate()
            if not self.anonymous:
                await self.get_profile()
            if self.stream_transport != "polling" and not self._socket.connected:
                try:
                    await self._connect_socket()
                except (socketio.exceptions.ConnectionError, TimeoutError, OSError):
                    self._ready.clear()
                    if self.stream_transport == "websocket":
                        raise ProtocolError("Socket.IO connection failed") from None
                    self._ready.set()
                    self._connection_event(
                        "polling", "WebSocket unavailable; REST data fallback active"
                    )
            elif self.stream_transport == "polling":
                self._ready.set()
                self._connection_event("polling", "REST polling selected")
            if self._supervisor is None or self._supervisor.done():
                self._supervisor = asyncio.create_task(self._supervise(), name="pybynix-supervisor")
            if not self.anonymous and (
                self._profile_supervisor is None or self._profile_supervisor.done()
            ):
                self._profile_supervisor = asyncio.create_task(
                    self._supervise_profile(), name="pybynix-profile-supervisor"
                )
            await self.wait_ready()
            return self

    async def _connect_socket(self):
        async with self._connect_attempt_lock:
            if self._socket.connected or self._closed:
                return
            if (self._dns or self._dns_servers or self._is_socks) and self._socket_http is None:
                if self._is_socks:
                    from aiohttp_socks import ProxyConnector

                    connector = ProxyConnector.from_url(
                        self._proxy.replace("socks5h://", "socks5://"), rdns=True
                    )
                else:
                    connector = aiohttp.TCPConnector(
                        resolver=StaticResolver(
                            self._dns, nameservers=self._dns_servers, port=self._dns_port
                        )
                    )
                self._socket_http = aiohttp.ClientSession(connector=connector)
                self._socket = self._make_socket(self._socket_http)
            await self._socket.connect(
                f"https://{self.host}",
                socketio_path="api/ws/socket.io",
                transports=["websocket"],
                wait_timeout=self.timeout,
            )

    async def _supervise(self):
        try:
            while not self._closed:
                now = time.monotonic()
                keys = tuple(self._streams)
                if self.stream_transport != "websocket":
                    semaphore = asyncio.Semaphore(4)

                    async def poll(key, limiter=semaphore):
                        async with limiter:
                            await self._poll_one(key)

                    due = []
                    for key in keys:
                        stale = now - self._last_tick.get(key, 0) > max(3, self.poll_interval * 3)
                        if (
                            self.stream_transport == "polling"
                            or not self._socket.connected
                            or stale
                        ):
                            due.append(poll(key))
                    if due:
                        await asyncio.gather(*due)
                # Native Socket.IO reconnects accidental disconnects first. This
                # watchdog also retries initial failures and server disconnects.
                if (
                    self.stream_transport != "polling"
                    and not self._socket.connected
                    and now >= self._socket_retry_at
                ):
                    self._socket_retry_at = now + 90
                    try:
                        await self._connect_socket()
                    except (socketio.exceptions.ConnectionError, TimeoutError, OSError, ValueError):
                        pass
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            return

    async def _supervise_profile(self):
        try:
            while not self._closed:
                delay = self.profile_refresh_interval - (
                    time.monotonic() - self._last_profile_refresh
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                try:
                    async with asyncio.timeout(self._profile_poll_timeout):
                        await self._get_profile(
                            timeout=self._profile_poll_timeout,
                            retries=0,
                        )
                except AuthenticationError:
                    self._connection_event("authentication_required", "Session refresh failed")
                except (APIError, ProtocolError, TimeoutError):
                    self._connection_event("health_degraded", "Profile health check failed")
                finally:
                    self._last_profile_refresh = time.monotonic()
        except asyncio.CancelledError:
            return

    async def _poll_one(self, key):
        try:
            async with asyncio.timeout(self._market_poll_timeout):
                data = await self._request(
                    f"synthetic/tick/{quote(key[0], safe='')}",
                    params={"interval": key[1]},
                    auth=False,
                    timeout=self._market_poll_timeout,
                    retries=0,
                )
            await self._on_tick(data)
            self.metrics["poll_fallbacks"] += 1
            self._ready.set()
        except (APIError, ProtocolError, TimeoutError):
            self._connection_event("data_unavailable", "REST fallback request failed")

    async def wait_ready(self, timeout=None):
        await asyncio.wait_for(self._ready.wait(), timeout or self.timeout)

    async def reconnect(self):
        if self._socket.connected:
            await self._socket.disconnect()
        self._ready.clear()
        return await self.connect()

    async def close(self):
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._ready.clear()
            if self._supervisor and self._supervisor is not asyncio.current_task():
                self._supervisor.cancel()
                await asyncio.gather(self._supervisor, return_exceptions=True)
            if self._profile_supervisor and self._profile_supervisor is not asyncio.current_task():
                self._profile_supervisor.cancel()
                await asyncio.gather(self._profile_supervisor, return_exceptions=True)
            try:
                await self._socket.shutdown()
            finally:
                if self._socket_http:
                    await self._socket_http.close()
                await self._http.aclose()
                for group in [
                    *self._streams.values(),
                    self._account_streams,
                    self._connection_streams,
                ]:
                    for sub in tuple(group):
                        sub.finish()
                self._streams.clear()
                self._account_streams.clear()
                self._connection_streams.clear()

    async def __aenter__(self):
        try:
            return await self.connect()
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args):
        await self.close()

    async def _request(
        self,
        path,
        *,
        method="GET",
        body=None,
        params=None,
        auth=True,
        timeout=None,
        retries=None,
    ):
        if self._closed:
            raise RuntimeError("Client is closed")
        if auth and self.anonymous:
            raise AuthenticationError(401, "authentication_required")
        refresh_attempted = False
        request_timeout = self.timeout if timeout is None else timeout
        retry_budget = self.read_retries if retries is None and method == "GET" else (retries or 0)
        for attempt in range(retry_budget + 2):
            token = self._session.token if self._session and auth else None
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self.metrics["requests"] += 1
            try:
                async with self._request_semaphore:
                    await self._pacer.wait()
                    response = await self._http.request(
                        method,
                        path.lstrip("/"),
                        json=body,
                        params=params,
                        headers=headers,
                        timeout=request_timeout,
                    )
            except httpx.HTTPError:
                if method == "GET" and attempt < retry_budget:
                    self.metrics["read_retries"] += 1
                    await asyncio.sleep(min(0.25 * 2**attempt, 2))
                    continue
                raise ProtocolError("HTTP transport failure") from None
            if response.status_code == 401 and auth:
                if not refresh_attempted and method == "GET" and self._provider:
                    refresh_attempted = True
                    await self._authenticate(token)
                    continue
                raise AuthenticationError(401)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if method == "GET" and attempt < retry_budget and retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = float("inf")
                    if math.isfinite(delay) and 0 <= delay <= 30:
                        self.metrics["read_retries"] += 1
                        await asyncio.sleep(delay)
                        continue
                raise RateLimitError(retry_after)
            if (
                response.status_code in {502, 503, 504}
                and method == "GET"
                and attempt < retry_budget
            ):
                self.metrics["read_retries"] += 1
                await asyncio.sleep(min(0.25 * 2**attempt, 2))
                continue
            if not response.is_success:
                raise APIError(response.status_code)
            try:
                data = response.json()
            except ValueError:
                raise ProtocolError("Expected JSON response") from None
            if path.lstrip("/") == "auth/me" and isinstance(data, dict):
                refreshed = data.get("refreshed_token")
                if (
                    refreshed
                    and isinstance(refreshed, str)
                    and self._session
                    and self._session.token == token
                ):
                    self._session = Session(refreshed, self.host)
                    if self._socket.connected:
                        await self._socket.emit("subscribe_account", {"token": refreshed})
            return data
        raise AuthenticationError(401)

    async def _get_profile(self, *, timeout=None, retries=None):
        data = await self._request("auth/me", timeout=timeout, retries=retries)
        if not isinstance(data, dict):
            raise ProtocolError("Expected profile object")
        identity = data.get("user_id") or data.get("account_id")
        if identity:
            fingerprint = hashlib.sha256(str(identity).encode()).hexdigest()
            if self._identity and self._identity != fingerprint:
                raise AuthenticationError(403, "account_changed")
            self._identity = fingerprint
            self._scope = self.host + ":" + fingerprint
        self._last_profile_refresh = time.monotonic()
        return {k: v for k, v in data.items() if k != "refreshed_token"}

    async def get_profile(self):
        return await self._get_profile()

    async def get_balance(self, account_type=None):
        account = AccountType(account_type or self.account_type)
        if account is AccountType.FUNDED:
            if not self.challenge_id:
                raise ValueError("Select challenge_id for a funded balance")
            challenge = await self.get_funded_challenge(self.challenge_id)
            value = challenge.get("challenge", challenge)
            return Decimal(str(value["current_balance"]))
        profile = await self.get_profile()
        field = (
            "total_balance"
            if account is AccountType.REAL and "total_balance" in profile
            else f"{account.value}_balance"
        )
        return Decimal(str(profile[field]))

    async def get_balance_breakdown(self):
        profile = await self.get_profile()
        keys = (
            "demo_balance",
            "real_balance",
            "bonus_balance",
            "deposit_balance",
            "profit_balance",
            "total_balance",
            "withdrawable_balance",
        )
        return {key: Decimal(str(profile[key])) for key in keys if profile.get(key) is not None}

    async def change_account(self, account_type):
        # Bynix selects the account in each order; no invented account-switch RPC.
        self.account_type = AccountType(account_type)

    async def get_assets(self, *, include_inactive=False):
        data = await self._request(
            "assets", auth=False, params={"include_inactive": str(include_inactive).lower()}
        )
        if not isinstance(data, list):
            raise ProtocolError("Expected an asset list")
        if not include_inactive:
            self._asset_cache = (time.monotonic(), data)
        return data

    async def get_asset(self, symbol):
        stamp, assets = self._asset_cache
        if not assets or time.monotonic() - stamp > 60:
            assets = await self.get_assets()
        for asset in assets:
            if symbol in {asset.get("trade_symbol"), asset.get("asset_id"), asset.get("name")}:
                return dict(asset)
        exact = [a for a in assets if a.get("symbol") == symbol]
        if len(exact) == 1:
            return dict(exact[0])
        raise ValueError("Unknown or ambiguous symbol; use trade_symbol from get_assets()")

    async def get_trading_rules(self):
        data = await self._request("trading-rules")
        if not isinstance(data, dict):
            raise ProtocolError("Expected trading rules object")
        self._rules_cache = (time.monotonic(), data)
        return data

    async def get_funded_plans(self):
        return await self._request("funded/plans", auth=False)

    async def get_funded_challenges(self):
        return (await self._request("funded/my-challenges"))["challenges"]

    async def get_active_funded(self):
        return (await self._request("funded/active"))["challenge"]

    async def get_funded_challenge(self, challenge_id):
        return await self._request(f"funded/challenges/{quote(str(challenge_id), safe='')}")

    async def get_tournaments(self):
        return await self._request("tournaments", auth=False)

    async def get_my_tournaments(self):
        return await self._request("tournaments/my")

    async def get_tournament(self, tournament_id):
        return await self._request(f"tournaments/{quote(str(tournament_id), safe='')}", auth=False)

    async def get_payouts(self):
        return {
            a.get("trade_symbol", a["name"]): Decimal(str(a["payout_percentage"]))
            for a in await self.get_assets()
        }

    async def get_platform_status(self):
        return await self._request("platform/status", auth=False)

    async def get_candles(self, symbol, *, interval="1m", count=100, before=None):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 500:
            raise ValueError("Count must be an integer from 1 to 500")
        params = {"symbol": symbol, "interval": _interval(interval), "count": count}
        if before is not None:
            if not isinstance(before, int) or isinstance(before, bool) or before <= 0:
                raise ValueError("before must be a positive Unix timestamp in seconds")
            params["before"] = before
        data = await self._request("synthetic/history", params=params, auth=False)
        if not isinstance(data.get("candles"), list):
            raise ProtocolError("Candle response lacks candles")
        candles = {c["timestamp"]: Candle.from_wire(c) for c in data["candles"]}
        return sorted(
            (c for c in candles.values() if before is None or c.timestamp < before),
            key=lambda c: c.timestamp,
        )[-count:]

    async def get_historical_candles(self, symbol, *, start, end, interval="1m", max_pages=100):
        if not isinstance(start, int) or not isinstance(end, int) or not 0 < start < end:
            raise ValueError("start/end must be increasing Unix seconds")
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        result = {}
        cursor = end
        for _ in range(max_pages):
            batch = await self.get_candles(symbol, interval=interval, count=500, before=cursor)
            if not batch:
                break
            for candle in batch:
                if start <= candle.timestamp < end:
                    result[candle.timestamp] = candle
            oldest = batch[0].timestamp
            if oldest <= start:
                break
            if oldest >= cursor:
                raise ProtocolError("History pagination did not advance")
            cursor = oldest
        else:
            raise ProtocolError("History page limit reached; narrow the requested range")
        return sorted(result.values(), key=lambda c: c.timestamp)

    async def subscribe_ticks(self, symbol, *, interval="1m"):
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A nonempty symbol is required")
        await self.wait_ready()
        key = (symbol, _interval(interval))
        if key not in self._streams and len(self._streams) >= self.max_subscriptions:
            raise ValueError("Subscription limit reached; close an unused stream first")

        async def remove(sub):
            group = self._streams.get(key)
            if group is not None:
                group.discard(sub)
                if not group:
                    self._streams.pop(key, None)
                    if self._socket.connected:
                        try:
                            await self._socket.emit(
                                "unsubscribe_synthetic", {"symbol": symbol, "interval": interval}
                            )
                        except (
                            socketio.exceptions.BadNamespaceError,
                            socketio.exceptions.ConnectionError,
                        ):
                            pass

        sub = Subscription(remove, self.queue_size)
        group = self._streams.setdefault(key, set())
        first = not group
        group.add(sub)
        if first and self._socket.connected:
            try:
                await self._socket.emit(
                    "subscribe_synthetic", {"symbol": symbol, "interval": interval}
                )
            except (socketio.exceptions.BadNamespaceError, socketio.exceptions.ConnectionError):
                if self.stream_transport == "websocket":
                    await sub.aclose()
                    raise ProtocolError("Socket disconnected while subscribing") from None
            except BaseException:
                await sub.aclose()
                raise
        return sub

    async def stream_ticks(self, symbol, *, interval="1m"):
        async with await self.subscribe_ticks(symbol, interval=interval) as sub:
            async for tick in sub:
                yield tick

    async def stream_candles(self, symbol, *, interval="1m", closed_only=False):
        async for tick in self.stream_ticks(symbol, interval=interval):
            candle = tick.closed if closed_only else tick.candle
            if candle is not None:
                yield candle

    def _local_subscription(self, group):
        async def remove(sub):
            group.discard(sub)

        sub = Subscription(remove, self.queue_size)
        if self._closed:
            sub.finish()
        else:
            group.add(sub)
        return sub

    def account_events(self):
        return self._local_subscription(self._account_streams)

    def connection_events(self):
        return self._local_subscription(self._connection_streams)

    async def get_realtime_sentiment(self, symbol):
        """Current platform-style derived display, not broker position counts."""
        now = time.monotonic()
        current = self._sentiment.get(symbol)
        tracker = current[0] if current else SentimentTracker()
        if current is None or now - current[1] >= 60:
            data = await self._request(
                "synthetic/history",
                auth=False,
                params={"symbol": symbol, "interval": "1m", "count": 30},
            )
            tracker.set_candles([Candle.from_wire(c) for c in data["candles"]])
            self._sentiment[symbol] = (tracker, now)
        return tracker.snapshot()

    async def stream_sentiment(self, symbol):
        await self.get_realtime_sentiment(symbol)
        async for _ in self.stream_ticks(symbol):
            yield await self.get_realtime_sentiment(symbol)

    async def get_candle_sentiment(self, symbol, *, count=30):
        candles = await self.get_candles(symbol, count=count)
        positive = negative = Decimal(0)
        for index, candle in enumerate(candles):
            if not candle.open:
                continue
            change = (candle.close - candle.open) / candle.open
            weight = Decimal("0.5") + Decimal(index) / len(candles)
            positive += max(change, Decimal(0)) * weight
            negative += max(-change, Decimal(0)) * weight
        total = positive + negative
        up = positive / total if total else Decimal("0.5")
        return {
            "up": up,
            "down": 1 - up,
            "source": "derived_candle_momentum",
            "samples": len(candles),
            "is_trader_positioning": False,
        }

    async def get_pending_orders(self):
        data = await self._request("trades/pending")
        return data["trades"]

    async def get_trade_history(self, *, account_type=None, page=1, limit=50):
        account = AccountType(account_type or self.account_type)
        if (
            not isinstance(page, int)
            or page < 1
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("page must be positive and limit 1..100")
        data = await self._request(
            "trades/history", params={"account_type": account.value, "page": page, "limit": limit}
        )
        return data["trades"]

    async def get_trade_stats(self):
        return await self._request("trades/stats")

    async def place_order(
        self,
        symbol,
        direction,
        amount,
        duration,
        *,
        account_type=None,
        client_ref=None,
        challenge_id=None,
    ):
        account = AccountType(account_type or self.account_type)
        if account is not AccountType.DEMO and not self.allow_real_trading:
            raise ValueError("Real trading requires allow_real_trading=True")
        challenge = challenge_id or self.challenge_id
        if account is AccountType.FUNDED and not challenge:
            raise ValueError("Funded execution requires challenge_id")
        if direction not in ("call", "put"):
            raise ValueError("Direction must be call or put")
        try:
            money = Decimal(str(amount))
            valid_money = (
                money.is_finite()
                and 0 < money <= Decimal(1000000000000)
                and money == money.quantize(Decimal("0.01"))
            )
        except (InvalidOperation, TypeError, ValueError):
            valid_money = False
        if not valid_money:
            raise ValueError("Amount must be positive with at most two decimal places")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 5:
            raise ValueError("Duration must be an integer number of seconds, at least 5")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A nonempty symbol is required")
        if self._scope == "unbound" and getattr(self._journal, "requires_identity", False):
            raise ValueError("Connect and verify the account before using a durable journal")
        if self.validate_orders:
            asset = await self.get_asset(symbol)
            symbol = asset.get("trade_symbol", asset["name"])
            if asset.get("is_active") is False or asset.get("locked") or asset.get("admin_locked"):
                raise ValueError("Asset is not currently available for orders")
            if asset.get("demo_only") and account is not AccountType.DEMO:
                raise ValueError("Asset is demo-only")
            stamp, rules = self._rules_cache
            if not rules or time.monotonic() - stamp > 60:
                rules = await self.get_trading_rules()
            if rules.get("enabled") and account is not AccountType.DEMO:
                is_real_market = asset.get("market") == "real"
                minimum = rules.get(
                    "min_expiry_real_market_s" if is_real_market else "min_expiry_otc_s", 5
                )
                if duration < minimum:
                    raise ValueError(f"This market requires duration >= {minimum} seconds")
                if (
                    account is not AccountType.DEMO
                    and is_real_market
                    and rules.get("block_real_market")
                ):
                    raise ValueError("Real market execution is restricted")
                cap = rules.get("max_stake_usd")
                if cap is not None and money > Decimal(str(cap)):
                    raise ValueError("Amount exceeds the platform stake limit")
        ref = client_ref or uuid.uuid4().hex
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ref):
            raise ValueError("client_ref must contain 1..128 safe identifier characters")
        # The observed schema requires a quote. Obtain it from Bynix, not the caller.
        latest = self._latest_prices.get(symbol)
        if latest and time.monotonic() - latest[1] <= 2:
            entry_price = latest[0]
        else:
            quote_data = await self._request(
                "synthetic/history",
                auth=False,
                params={"symbol": symbol, "interval": "1m", "count": 1},
            )
            entry_price = Decimal(str(quote_data["price"]))
        if not entry_price.is_finite() or entry_price <= 0:
            raise ProtocolError("Bynix returned an invalid entry quote")
        body = {
            "asset": symbol,
            "trade_type": direction,
            "direction": "up" if direction == "call" else "down",
            "amount": float(money),
            "duration": duration,
            "account_type": account.value,
            "client_ref": ref,
            "entry_price": float(entry_price),
        }
        if account is AccountType.FUNDED:
            body["challenge_id"] = challenge
        await self._journal.reserve(self._scope, ref, body)
        try:
            data = await self._request("trades", method="POST", body=body)
        except (ProtocolError, asyncio.CancelledError):
            await asyncio.shield(self._journal.update(self._scope, ref, state="unknown"))
            raise OrderOutcomeUnknown(ref) from None
        except APIError as error:
            if error.status >= 500:
                await self._journal.update(self._scope, ref, state="unknown")
                raise OrderOutcomeUnknown(ref) from None
            await self._journal.update(self._scope, ref, state="rejected")
            raise
        if not isinstance(data, dict) or not (
            data.get("trade_id") or isinstance(data.get("trade"), dict)
        ):
            await self._journal.update(self._scope, ref, state="unknown")
            raise OrderOutcomeUnknown(ref)
        trade_id = data.get("trade_id") or data["trade"].get("trade_id")
        if not trade_id:
            await self._journal.update(self._scope, ref, state="unknown")
            raise OrderOutcomeUnknown(ref)
        self._known_orders[ref] = trade_id
        if len(self._known_orders) > 1000:
            self._known_orders.popitem(last=False)
        receipt = {**data, "client_ref": ref}
        try:
            await self._journal.update(self._scope, ref, state="accepted", trade_id=trade_id)
        except (Exception, asyncio.CancelledError):
            # The external side effect is confirmed. Preserve that fact even if
            # the optional persistence boundary fails or is cancelled.
            raise OrderJournalError(receipt) from None
        return receipt

    async def buy(self, amount, asset, direction, duration, **kwargs):
        return await self.place_order(asset, direction, amount, duration, **kwargs)

    async def reconcile_order(
        self, *, trade_id=None, client_ref=None, account_type=None, max_pages=5
    ):
        if not trade_id and not client_ref:
            raise ValueError("Supply trade_id or client_ref")
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be positive")
        if trade_id is None and client_ref is not None:
            trade_id = self._known_orders.get(client_ref)
            recorded = await self._journal.get(self._scope, client_ref)
            if recorded:
                trade_id = trade_id or recorded.get("trade_id")
                account_type = account_type or recorded["payload"].get("account_type")

        def matches(row):
            return (trade_id is not None and row.get("trade_id") == trade_id) or (
                client_ref is not None and row.get("client_ref") == client_ref
            )

        for order in await self.get_pending_orders():
            if matches(order):
                if client_ref:
                    await self._journal.update(
                        self._scope, client_ref, state="accepted", trade_id=order.get("trade_id")
                    )
                return order
        for page in range(1, max_pages + 1):
            history = await self.get_trade_history(account_type=account_type, page=page, limit=100)
            for order in history:
                if matches(order):
                    if client_ref:
                        await self._journal.update(
                            self._scope, client_ref, state="settled", trade_id=order.get("trade_id")
                        )
                    return order
            if len(history) < 100:
                break
        return None  # Absence is not evidence the order never executed.

    async def unresolved_orders(self):
        return await self._journal.unresolved(self._scope)

    async def get_tick(self, symbol, *, interval="1m"):
        data = await self._request(
            f"synthetic/tick/{quote(symbol, safe='')}",
            params={"interval": _interval(interval)},
            auth=False,
        )
        try:
            return Tick.from_wire(data)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise ProtocolError("Malformed tick response") from None

    async def wait_order_result(self, trade_id, *, account_type=None, timeout=120, poll_interval=2):
        if timeout <= 0 or poll_interval <= 0 or not math.isfinite(timeout + poll_interval):
            raise ValueError("Timeout and polling interval must be finite and positive")
        async with asyncio.timeout(timeout):
            while True:
                order = await self.reconcile_order(trade_id=trade_id, account_type=account_type)
                if order and order.get("status") in ("won", "lost", "draw", "refunded", "closed"):
                    await self._journal.update_trade(self._scope, trade_id, "settled")
                    return order
                await asyncio.sleep(poll_interval)

    async def close_trade_early(self, trade_id):
        order = await self.reconcile_order(trade_id=trade_id)
        if not order:
            raise ValueError("Order could not be verified")
        if order.get("account_type") != "demo" and not self.allow_real_trading:
            raise ValueError("Real trading requires allow_real_trading=True")
        try:
            result = await self._request(
                f"trades/{quote(str(trade_id), safe='')}/close-early", method="POST"
            )
            await self._journal.update_trade(self._scope, trade_id, "settled")
            return result
        except (ProtocolError, asyncio.CancelledError):
            raise OrderOutcomeUnknown(str(trade_id)) from None
        except APIError as error:
            if error.status >= 500:
                raise OrderOutcomeUnknown(str(trade_id)) from None
            raise

    async def get_server_clock(self, symbol="EUR/USD OTC"):
        start = time.monotonic()
        wall_start = time.time() * 1000
        data = await self._request(
            "synthetic/history", auth=False, params={"symbol": symbol, "interval": "1m", "count": 1}
        )
        elapsed = (time.monotonic() - start) * 1000
        return {
            "server_time_ms": data["server_time"],
            "round_trip_ms": elapsed,
            "estimated_offset_ms": data["server_time"] - (wall_start + elapsed / 2),
            "uncertainty_ms": elapsed / 2,
        }
