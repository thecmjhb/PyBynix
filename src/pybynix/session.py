"""Host-bound session lifecycle: single-flight renewal, optional storage, background upkeep.

The manager never launches a browser and never reads credentials itself. Login is
delegated to a caller-supplied ``SessionProvider`` (for example ``PasswordAuth`` or a
``BrowserAuth`` the user operates), so challenges still need a human.

A manager is a ``SessionProvider`` too, so it can be passed straight to ``Bynix``.
Use one manager per account; never share a token between accounts.
"""

import asyncio
import base64
import binascii
import json
import math
import time
from pathlib import Path
from typing import Protocol

from .auth import SessionProvider, load_session, save_session
from .client import _host
from .errors import AuthenticationError, ProtocolError, RateLimitError
from .models import Session


def token_expiry(token: str) -> float | None:
    """Return the JWT ``exp`` claim as Unix seconds, or None when it is absent.

    The signature is not verified. The value only schedules proactive renewal; it is
    never used to decide that a token is authorized.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        return None
    return float(exp)


class SessionStore(Protocol):
    def load(self) -> Session | None: ...

    def save(self, session: Session) -> None: ...


class FileSessionStore:
    """Host-bound atomic session file (0600 where the platform supports it)."""

    def __init__(self, path: str | Path, host: str):
        self.path = Path(path).expanduser()
        self.host = _host(host)

    def load(self) -> Session | None:
        if not self.path.exists():
            return None
        try:
            session = load_session(self.path)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return session if session.host.lower() == self.host else None

    def save(self, session: Session) -> None:
        save_session(session, self.path)


class BynixSessionManager:
    """Owns one account's session: renews it once, shares it, and keeps it fresh.

    * ``get_session()`` returns the cached token, loading a stored one first, and renews
      only when the token is missing or inside ``refresh_margin`` of its JWT expiry.
    * ``refresh(stale_token)`` renews after a 401/403. Concurrent callers that saw the
      same stale token share one login. A login completed within ``coalesce_seconds``
      is reused for a caller that did not name a stale token.
    * ``run()`` keeps the token renewed in the background. It backs off on transient
      failures, honours ``Retry-After``, and stops on authentication failures or
      challenges so that no loop keeps hitting the login endpoint.
    """

    def __init__(
        self,
        host: str,
        provider: SessionProvider,
        *,
        store: SessionStore | None = None,
        refresh_margin: float = 300.0,
        coalesce_seconds: float = 10.0,
        idle_interval: float = 300.0,
        max_backoff: float = 300.0,
        clock=time.time,
    ):
        self.host = _host(host)
        if not callable(provider):
            raise TypeError("provider must be a SessionProvider")
        for name, value, minimum in (
            ("refresh_margin", refresh_margin, 0.0),
            ("coalesce_seconds", coalesce_seconds, 1.0),
            ("idle_interval", idle_interval, 1.0),
            ("max_backoff", max_backoff, 1.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < minimum
            ):
                raise ValueError(f"{name} must be finite and at least {minimum:g}")
        if max_backoff < coalesce_seconds:
            raise ValueError("max_backoff must be at least coalesce_seconds")
        self._provider = provider
        self._store = store
        self.refresh_margin = float(refresh_margin)
        self.coalesce_seconds = float(coalesce_seconds)
        self.idle_interval = float(idle_interval)
        self.max_backoff = float(max_backoff)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._current: Session | None = None
        self._issued_at = float("-inf")

    @property
    def session(self) -> Session | None:
        """The cached session, without triggering renewal."""
        return self._current

    async def __call__(self, host: str) -> Session:
        """``SessionProvider`` protocol, so ``Bynix(provider=manager)`` works."""
        if _host(host) != self.host:
            raise ValueError("Session manager is bound to a different host")
        return await self.refresh()

    async def get_session(self) -> Session:
        session = self._current
        if session is not None and not self._due(session):
            return session
        async with self._lock:
            if self._current is None:
                stored = self._stored()
                if stored is not None:
                    self._remember(stored, issued_at=float("-inf"))
            session = self._current
            if session is not None and not self._due(session):
                return session
            return await self._renew_locked(session.token if session else None)

    async def refresh(self, stale_token: str | None = None) -> Session:
        async with self._lock:
            return await self._renew_locked(stale_token)

    def adopt(self, session: Session) -> None:
        """Record a session renewed elsewhere, such as a client's ``refreshed_token``."""
        self._check_host(session)
        self._remember(session)

    def invalidate(self, token: str) -> None:
        """Forget the cached token when it matches, so the next call renews it."""
        if self._current is not None and self._current.token == token:
            self._current = None

    async def run(self) -> None:
        """Keep the session renewed until cancelled. Raises on authentication failure."""
        failures = 0
        while True:
            try:
                session = await self.get_session()
            except AuthenticationError:
                raise
            except (ProtocolError, RateLimitError, OSError, TimeoutError) as error:
                failures += 1
                delay = min(self.max_backoff, self.coalesce_seconds * 2 ** (failures - 1))
                retry_after = _retry_after(error)
                if retry_after is not None:
                    delay = max(delay, retry_after)
                await asyncio.sleep(delay)
                continue
            failures = 0
            await asyncio.sleep(self._sleep_for(session))

    def _due(self, session: Session) -> bool:
        if self._clock() - self._issued_at < self.coalesce_seconds:
            return False
        expires = token_expiry(session.token)
        if expires is None:
            return False
        return self._clock() >= expires - self.refresh_margin

    def _sleep_for(self, session: Session) -> float:
        expires = token_expiry(session.token)
        if expires is None:
            return self.idle_interval
        return max(self.coalesce_seconds, expires - self.refresh_margin - self._clock())

    async def _renew_locked(self, stale_token: str | None) -> Session:
        current = self._current
        if current is not None:
            if stale_token is not None:
                if current.token != stale_token:
                    return current
            elif self._clock() - self._issued_at < self.coalesce_seconds:
                return current
        try:
            session = await self._provider(self.host)
        except AuthenticationError:
            # The token a caller could not use is no longer worth keeping.
            if current is not None and (stale_token is None or current.token == stale_token):
                self._current = None
            raise
        self._check_host(session)
        self._remember(session)
        return session

    def _check_host(self, session: Session) -> None:
        if not isinstance(session, Session) or session.host.lower() != self.host:
            raise ValueError("Auth provider returned a session for a different host")

    def _remember(self, session: Session, issued_at: float | None = None) -> None:
        self._current = session
        self._issued_at = self._clock() if issued_at is None else issued_at
        if self._store is not None and issued_at is None:
            self._store.save(session)

    def _stored(self) -> Session | None:
        if self._store is None:
            return None
        session = self._store.load()
        if session is None or session.host.lower() != self.host:
            return None
        return session

    def __repr__(self) -> str:
        state = "loaded" if self._current is not None else "empty"
        return f"BynixSessionManager(host={self.host!r}, session={state})"


def _retry_after(error: BaseException) -> float | None:
    value = getattr(error, "retry_after", None)
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
