"""Explicit session, credential and browser providers. No implicit browser launch."""

import json
import os
from pathlib import Path
from typing import Protocol

import httpx

from .errors import AuthenticationError, ChallengeRequired, ProtocolError, RateLimitError
from .models import Session
from .network import DNSHTTPTransport, validate_dns


class SessionProvider(Protocol):
    async def __call__(self, host: str) -> Session: ...


class PasswordAuth:
    def __init__(self, email: str, password: str, *, proxy=None, dns_overrides=None):
        self._email, self._password = email, password
        if proxy and dns_overrides:
            raise ValueError("Use proxy or static DNS overrides, not both")
        self._proxy = proxy
        self._dns = validate_dns(dns_overrides)

    async def __call__(self, host: str) -> Session:
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,
                trust_env=False,
                proxy=self._proxy,
                transport=DNSHTTPTransport(self._dns) if self._dns else None,
            ) as http:
                response = await http.post(
                    f"https://{host}/api/auth/login",
                    json={"email": self._email, "password": self._password},
                )
        except httpx.TransportError:
            raise ProtocolError("Login transport failed") from None
        if not response.is_success:
            if response.status_code == 429:
                raise RateLimitError(response.headers.get("Retry-After"))
            if response.status_code == 403:
                try:
                    failure = response.json()
                except ValueError:
                    failure = {}
                if (
                    response.headers.get("cf-mitigated") == "challenge"
                    or isinstance(failure, dict)
                    and failure.get("code")
                    in {"challenge_required", "captcha_required", "turnstile_required"}
                ):
                    raise ChallengeRequired()
            raise AuthenticationError(response.status_code)
        data = response.json()
        if data.get("requires_2fa"):
            raise ChallengeRequired(data.get("method"))
        if not data.get("access_token"):
            raise ProtocolError("Login response lacks an access token")
        return Session(data["access_token"], host)


class BrowserAuth:
    """Optional Playwright provider; visible browser permits manual challenges.

    Bynix web app uses localStorage['token']. The result is subsequently validated
    by the client's /auth/me request, never assumed authenticated from the page URL.
    """

    def __init__(self, email=None, password=None, *, headless=False, timeout=180):
        self._email, self._password = email, password
        self.headless, self.timeout = headless, timeout

    async def __call__(self, host):
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                page = await browser.new_page()
                await page.goto(f"https://{host}/login", wait_until="domcontentloaded")
                if self._email and self._password:
                    await page.get_by_test_id("login-email").fill(self._email)
                    await page.get_by_test_id("login-password").fill(self._password)
                    await page.get_by_test_id("login-submit").click()
                await page.wait_for_function(
                    "Boolean(localStorage.getItem('token'))", timeout=self.timeout * 1000
                )
                token = await page.evaluate("localStorage.getItem('token')")
                return Session(token, host)
            finally:
                await browser.close()


class AuthChain:
    """Fall back only on an interactive challenge, not bad credentials/rate limits."""

    def __init__(self, *providers: SessionProvider):
        if not providers:
            raise ValueError("At least one provider is required")
        self.providers = providers

    async def __call__(self, host):
        for provider in self.providers:
            try:
                return await provider(host)
            except ChallengeRequired:
                continue
        raise ChallengeRequired()


def load_session(path: str | Path) -> Session:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Session(data["token"], data["host"])


def save_session(session: Session, path: str | Path):
    """Opt-in plaintext export; protect the containing directory on Windows."""
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"host": session.host, "token": session.token}, stream)
