"""Bynix's observed REST + Socket.IO protocol, independently implemented."""

import asyncio
import json
import math
import re
import time
import uuid
from collections import OrderedDict
from decimal import Decimal
from importlib.resources import files
from urllib.parse import quote

import aiohttp
import httpx
import socketio

from .auth import SessionProvider
from .errors import (
    APIError,
    AuthenticationError,
    CapabilityError,
    OrderOutcomeUnknown,
    ProtocolError,
    RateLimitError,
)
from .models import AccountType, Candle, ConnectionEvent, Session, Tick
from .network import DNSHTTPTransport, StaticResolver, validate_dns
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
        queue_size=256,
    ):
        self.host = _host(host)
        known = set(json.loads(files("pybynix").joinpath("domains.json").read_text()))
        known.update(_host(h) for h in trusted_hosts)
        if self.host not in known:
            raise ValueError("Host must be explicitly trusted")
        if session and _host(session.host) != self.host:
            raise ValueError("Session host mismatch; authenticate separately on the new host")
        if timeout <= 0 or queue_size < 1:
            raise ValueError("Timeout and queue size must be positive")
        self._session, self._provider = session, provider
        self.account_type = AccountType(account_type)
        self.allow_real_trading = allow_real_trading
        self.timeout, self.queue_size = timeout, queue_size
        if proxy and dns_overrides:
            raise ValueError("Use proxy or static DNS overrides, not both")
        if proxy and not proxy.startswith(("http://", "https://")):
            raise ValueError("Only HTTP(S) CONNECT proxies are supported")
        self._proxy = proxy
        self._dns = validate_dns(dns_overrides)
        self._socket_http = None
        self._http = httpx.AsyncClient(
            base_url=f"https://{self.host}/api/",
            timeout=timeout,
            follow_redirects=False,
            proxy=proxy,
            transport=DNSHTTPTransport(self._dns) if self._dns else None,
            trust_env=False,
        )
        self._socket = self._make_socket()
        self._auth_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._closed = False
        self._generation = 0
        self._streams: dict[tuple, set[Subscription]] = {}
        self._latest_prices = {}
        self._known_orders = OrderedDict()
        self._account_streams: set[Subscription] = set()
        self._connection_streams: set[Subscription] = set()
        self.metrics = {"ticks": 0, "malformed_ticks": 0, "connections": 0, "requests": 0}

    def _make_socket(self, http_session=None):
        client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=8,
            reconnection_delay=1,
            reconnection_delay_max=20,
            logger=False,
            engineio_logger=False,
            http_session=http_session,
            websocket_extra_options={"proxy": self._proxy} if self._proxy else {},
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
        self._latest_prices[tick.symbol] = (tick.price, time.monotonic())
        for sub in tuple(self._streams.get((tick.symbol, tick.interval), ())):
            sub.put(tick)

    async def _on_account(self, data):
        if isinstance(data, dict):
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
            self._session = session
            if self._socket.connected:
                await self._socket.emit("subscribe_account", {"token": session.token})

    async def connect(self):
        async with self._connect_lock:
            if self._closed:
                raise RuntimeError("Client is closed; create a new client")
            if self.connected:
                return self
            if not self._session:
                await self._authenticate()
            await self.get_profile()
            if not self._socket.connected:
                if self._dns and self._socket_http is None:
                    self._socket_http = aiohttp.ClientSession(
                        connector=aiohttp.TCPConnector(resolver=StaticResolver(self._dns))
                    )
                    self._socket = self._make_socket(self._socket_http)
                try:
                    await self._socket.connect(
                        f"https://{self.host}",
                        socketio_path="api/ws/socket.io",
                        transports=["websocket"],
                        wait_timeout=self.timeout,
                    )
                except (socketio.exceptions.ConnectionError, TimeoutError, OSError):
                    self._ready.clear()
                    raise ProtocolError("Socket.IO connection failed") from None
            await self.wait_ready()
            return self

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

    async def _request(self, path, *, method="GET", body=None, params=None, auth=True):
        if self._closed:
            raise RuntimeError("Client is closed")
        for attempt in range(2):
            token = self._session.token if self._session and auth else None
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self.metrics["requests"] += 1
            try:
                response = await self._http.request(
                    method, path.lstrip("/"), json=body, params=params, headers=headers
                )
            except httpx.HTTPError:
                raise ProtocolError("HTTP transport failure") from None
            if response.status_code == 401 and auth:
                if attempt == 0 and method == "GET" and self._provider:
                    await self._authenticate(token)
                    continue
                raise AuthenticationError(401)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise RateLimitError(retry_after)
            if not response.is_success:
                raise APIError(response.status_code)
            try:
                return response.json()
            except ValueError:
                raise ProtocolError("Expected JSON response") from None
        raise AuthenticationError(401)

    async def get_profile(self):
        data = await self._request("auth/me")
        # Bynix sometimes rotates the token in an authenticated profile response.
        if isinstance(data.get("refreshed_token"), str) and data["refreshed_token"]:
            self._session = Session(data["refreshed_token"], self.host)
            if self._socket.connected:
                await self._socket.emit("subscribe_account", {"token": self._session.token})
        return {k: v for k, v in data.items() if k != "refreshed_token"}

    async def get_balance(self, account_type=None):
        account = AccountType(account_type or self.account_type)
        profile = await self.get_profile()
        return Decimal(str(profile[f"{account.value}_balance"]))

    async def change_account(self, account_type):
        # Bynix selects the account in each order; no invented account-switch RPC.
        self.account_type = AccountType(account_type)

    async def get_assets(self, *, include_inactive=False):
        data = await self._request(
            "assets", auth=False, params={"include_inactive": str(include_inactive).lower()}
        )
        if not isinstance(data, list):
            raise ProtocolError("Expected an asset list")
        return data

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

        async def remove(sub):
            group = self._streams.get(key)
            if group is not None:
                group.discard(sub)
                if not group:
                    self._streams.pop(key, None)
                    if self._socket.connected:
                        await self._socket.emit(
                            "unsubscribe_synthetic", {"symbol": symbol, "interval": interval}
                        )

        sub = Subscription(remove, self.queue_size)
        group = self._streams.setdefault(key, set())
        first = not group
        group.add(sub)
        if first:
            try:
                await self._socket.emit(
                    "subscribe_synthetic", {"symbol": symbol, "interval": interval}
                )
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

    async def get_realtime_sentiment(self, *args, **kwargs):
        raise CapabilityError(
            "No upstream sentiment event verified; use get_candle_sentiment() "
            "for an explicitly derived candle momentum measure"
        )

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
        self, symbol, direction, amount, duration, *, account_type=None, client_ref=None
    ):
        account = AccountType(account_type or self.account_type)
        if account is AccountType.REAL and not self.allow_real_trading:
            raise ValueError("Real trading requires allow_real_trading=True")
        if direction not in ("call", "put"):
            raise ValueError("Direction must be call or put")
        money = Decimal(str(amount))
        if not money.is_finite() or money <= 0 or money != money.quantize(Decimal("0.01")):
            raise ValueError("Amount must be positive with at most two decimal places")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 5:
            raise ValueError("Duration must be an integer number of seconds, at least 5")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A nonempty symbol is required")
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
        try:
            data = await self._request("trades", method="POST", body=body)
        except (ProtocolError, asyncio.CancelledError):
            raise OrderOutcomeUnknown(ref) from None
        except APIError as error:
            if error.status >= 500:
                raise OrderOutcomeUnknown(ref) from None
            raise
        if not isinstance(data, dict) or not (
            data.get("trade_id") or isinstance(data.get("trade"), dict)
        ):
            raise OrderOutcomeUnknown(ref)
        trade_id = data.get("trade_id") or data["trade"].get("trade_id")
        if not trade_id:
            raise OrderOutcomeUnknown(ref)
        self._known_orders[ref] = trade_id
        if len(self._known_orders) > 1000:
            self._known_orders.popitem(last=False)
        return {**data, "client_ref": ref}

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

        def matches(row):
            return (trade_id is not None and row.get("trade_id") == trade_id) or (
                client_ref is not None and row.get("client_ref") == client_ref
            )

        for order in await self.get_pending_orders():
            if matches(order):
                return order
        for page in range(1, max_pages + 1):
            history = await self.get_trade_history(account_type=account_type, page=page, limit=100)
            for order in history:
                if matches(order):
                    return order
            if len(history) < 100:
                break
        return None  # Absence is not evidence the order never executed.

    async def wait_order_result(self, trade_id, *, account_type=None, timeout=120, poll_interval=2):
        if timeout <= 0 or poll_interval <= 0 or not math.isfinite(timeout + poll_interval):
            raise ValueError("Timeout and polling interval must be finite and positive")
        async with asyncio.timeout(timeout):
            while True:
                order = await self.reconcile_order(trade_id=trade_id, account_type=account_type)
                if order and order.get("status") in ("won", "lost", "draw", "refunded", "closed"):
                    return order
                await asyncio.sleep(poll_interval)

    async def close_trade_early(self, trade_id):
        order = await self.reconcile_order(trade_id=trade_id)
        if not order:
            raise ValueError("Order could not be verified")
        if order.get("account_type") != "demo" and not self.allow_real_trading:
            raise ValueError("Real trading requires allow_real_trading=True")
        try:
            return await self._request(
                f"trades/{quote(str(trade_id), safe='')}/close-early", method="POST"
            )
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
