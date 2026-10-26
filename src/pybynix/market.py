"""Market session: the gate in front of Bynix market data.

Observed 26 October 2026. Market endpoints stopped answering anonymously:

    GET /api/synthetic/history                      -> 401 {"detail":"Market session required"}
    GET /api/synthetic/history  (bogus sid)         -> 401 {"detail":"Market session expired"}

They now require an ``X-Market-Session`` header, and the value comes from a second
call that also hands out the key used to seal the response body:

    POST /api/market/session   (Bearer token, or no token for a guest session)
      body {"webdriver": false, "lang": "en-US", "touch": 0}
      -> {"sid": "...", "key": "<b64 32B>", "ttl": 43200, "guest": false,
          "limits": {...}, "stream": {"e": "<sealed keyring>"}}

    GET  /api/synthetic/history   with X-Market-Session: sid
      -> {"e": "<b64 nonce||ct||tag>"}   -> open with `key` -> the usual candle JSON

``stream.e`` opens to a rotating keyring for socket ticks::

    {"id": 2988341, "key": "<b64>", "next_id": 2988342, "next_key": "<b64>",
     "rotate_at": 1793005200, "epoch_s": 600}

and ticks arrive as ``{"k": <key id>, "e": "<sealed JSON>"}``.

Nothing here seals anything: the SDK only ever opens envelopes.
"""

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .crypto import ChaCha20Poly1305Error, decrypt_json
from .errors import APIError, ProtocolError

__all__ = ["MarketLimits", "MarketSession", "MarketSessionManager"]

# A browser reports these three; the endpoint accepts a session either way, but the
# web client sends them and a mismatch is an avoidable signal.
DEFAULT_FINGERPRINT = {"webdriver": False, "lang": "en-US", "touch": 0}

Transport = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class MarketLimits:
    """What this session is allowed to do, straight from the platform."""

    history_per_minute: int | None = None
    symbols_per_hour: int | None = None
    subscriptions_per_minute: int | None = None
    max_candles: int | None = None

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict):
            return cls()
        return cls(
            history_per_minute=value.get("history_1m"),
            symbols_per_hour=value.get("symbols_1h"),
            subscriptions_per_minute=value.get("subs_1m"),
            max_candles=value.get("candles"),
        )


@dataclass(frozen=True, slots=True)
class MarketSession:
    sid: str
    key: bytes = field(repr=False)
    expires_at: float = 0.0
    guest: bool = True
    limits: MarketLimits = field(default_factory=MarketLimits)
    stream_id: int | None = None
    stream_key: bytes | None = field(default=None, repr=False)
    next_stream_id: int | None = None
    next_stream_key: bytes | None = field(default=None, repr=False)
    rotate_at: float = 0.0

    def is_fresh(self, *, margin: float = 30.0) -> bool:
        return bool(self.sid) and time.time() < self.expires_at - margin

    def stream_key_for(self, key_id):
        if key_id is None:
            return None
        if key_id == self.stream_id:
            return self.stream_key
        if key_id == self.next_stream_id:
            return self.next_stream_key
        return None

    def describe(self):
        return {
            "guest": self.guest,
            "expires_in_s": round(max(0.0, self.expires_at - time.time()), 1),
            "limits": {
                "history_1m": self.limits.history_per_minute,
                "symbols_1h": self.limits.symbols_per_hour,
                "subs_1m": self.limits.subscriptions_per_minute,
                "candles": self.limits.max_candles,
            },
            "stream_id": self.stream_id,
        }


class MarketSessionManager:
    """Owns one market session and its keyring; refreshes it before it expires.

    `transport` performs the HTTP call and returns parsed JSON. The client supplies it
    so this module stays independent of httpx and of the auth layer.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        fingerprint: dict | None = None,
        ttl_margin: float = 60.0,
        refresh_ahead: float = 0.0,
    ):
        self._transport = transport
        self._fingerprint = dict(fingerprint or DEFAULT_FINGERPRINT)
        self._ttl_margin = float(ttl_margin)
        self._refresh_ahead = float(refresh_ahead)
        self._session: MarketSession | None = None
        self._lock = asyncio.Lock()
        self._generation = 0
        self.metrics = {"created": 0, "refreshes": 0, "decrypted": 0, "decrypt_errors": 0}

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def current(self):
        return self._session

    @property
    def generation(self):
        return self._generation

    async def ensure(self):
        """Return a live session, creating one only when needed."""
        session = self._session
        if session is not None and session.is_fresh(margin=self._ttl_margin):
            return session
        generation = self._generation
        async with self._lock:
            if self._generation != generation:
                return self._session  # another coroutine refreshed while we queued
            session = self._session
            if session is not None and session.is_fresh(margin=self._ttl_margin):
                return session
            return await self._create()

    async def refresh(self):
        """Force a new session (used after 401 Market session expired)."""
        generation = self._generation
        async with self._lock:
            if self._generation != generation:
                return self._session
            return await self._create()

    def invalidate(self):
        self._session = None
        self._generation += 1

    def _create(self):
        return self._create_session()

    async def _create_session(self):
        try:
            data = await self._transport(
                "POST", "market/session", json=self._fingerprint, auth=True, allow_anonymous=True
            )
        except APIError as exc:
            if exc.status == 404:
                # The endpoint is absent on this host: fall back to the old open mode.
                self._session = None
                raise ProtocolError("This host does not issue market sessions") from None
            raise
        if not isinstance(data, dict) or not data.get("sid"):
            raise ProtocolError("Market session response lacks a sid")
        try:
            key = base64.b64decode(data.get("key") or "")
        except (ValueError, TypeError):
            key = b""
        if len(key) != 32:
            raise ProtocolError("Market session key is not 32 bytes")

        stream = data.get("stream")
        stream_fields = {}
        if isinstance(stream, dict) and isinstance(stream.get("e"), str):
            try:
                config = decrypt_json(key, stream["e"])
            except ChaCha20Poly1305Error:
                config = None
            if isinstance(config, dict):
                stream_fields = {
                    "stream_id": config.get("id"),
                    "stream_key": _maybe_b64(config.get("key")),
                    "next_stream_id": config.get("next_id"),
                    "next_stream_key": _maybe_b64(config.get("next_key")),
                    "rotate_at": float(config.get("rotate_at") or 0.0),
                }

        ttl = float(data.get("ttl") or 0) or 3600.0
        session = MarketSession(
            sid=str(data["sid"]),
            key=key,
            expires_at=time.time() + ttl,
            guest=bool(data.get("guest", True)),
            limits=MarketLimits.from_wire(data.get("limits")),
            **stream_fields,
        )
        self._session = session
        self._generation += 1
        self.metrics["created"] += 1
        if self.metrics["created"] > 1:
            self.metrics["refreshes"] += 1
        return session

    # ── payload helpers ──────────────────────────────────────────────────────
    def is_sealed(self, data):
        return isinstance(data, dict) and isinstance(data.get("e"), str)

    def open(self, data, *, session=None):
        """Return the plaintext of a sealed payload, or `data` unchanged.

        Pass the session that made the request: a concurrent 401 can refresh the
        session and swap the key while a response sealed with the old key is still in
        flight.
        """
        if not self.is_sealed(data):
            return data
        active = session or self._session
        if active is None:
            raise ProtocolError("A sealed payload arrived with no market session")
        try:
            opened = decrypt_json(active.key, data["e"])
        except ChaCha20Poly1305Error as exc:
            self.metrics["decrypt_errors"] += 1
            raise ProtocolError(f"Could not open the market payload: {exc}") from None
        self.metrics["decrypted"] += 1
        return opened

    def stream_key(self, key_id):
        return self._session.stream_key_for(key_id) if self._session else None

    async def ensure_stream_key(self, key_id):
        """Look up a tick key; refresh the keyring once if the id is unknown."""
        key = self.stream_key(key_id)
        if key is not None:
            return key
        session = self._session
        if session is not None and session.rotate_at and time.time() >= session.rotate_at:
            await self.refresh()
            return self.stream_key(key_id)
        return None

    def stats(self):
        return {
            "session": self._session.describe() if self._session else None,
            "metrics": dict(self.metrics),
        }


def _maybe_b64(value):
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value)
    except (ValueError, TypeError):
        return None
    return raw if len(raw) == 32 else None
