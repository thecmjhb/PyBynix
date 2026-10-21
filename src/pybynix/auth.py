"""Explicit session, credential and browser providers. No implicit browser launch."""

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from .errors import AuthenticationError, ChallengeRequired, ProtocolError, RateLimitError
from .models import LoginChallenge, Session
from .network import DNSHTTPTransport, validate_dns, validate_dns_servers


class SessionProvider(Protocol):
    async def __call__(self, host: str) -> Session: ...


class PasswordAuth:
    def __init__(
        self,
        email: str,
        password: str,
        *,
        proxy=None,
        dns_overrides=None,
        otp_callback=None,
        dns_servers=(),
        dns_port=53,
    ):
        self._email, self._password = email, password
        if proxy and (dns_overrides or dns_servers):
            raise ValueError("Use proxy or static DNS overrides, not both")
        self._proxy = proxy
        self._dns = validate_dns(dns_overrides)
        self._dns_servers = validate_dns_servers(dns_servers)
        self._dns_port = dns_port
        self._otp_callback = otp_callback

    async def __call__(self, host: str) -> Session:
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,
                trust_env=False,
                proxy=self._proxy,
                transport=DNSHTTPTransport(
                    self._dns, nameservers=self._dns_servers, port=self._dns_port
                )
                if self._dns or self._dns_servers
                else None,
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
        if not isinstance(data, dict):
            raise ProtocolError("Expected login object")
        if data.get("requires_2fa"):
            if not self._otp_callback:
                raise ChallengeRequired(data.get("method"))
            challenge = LoginChallenge(data["challenge_id"], data.get("method", "unknown"))
            code = await self._otp_callback(challenge)
            if not isinstance(code, str) or not code.strip():
                raise ValueError("OTP callback must return a nonempty code")
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,
                trust_env=False,
                proxy=self._proxy,
                transport=DNSHTTPTransport(
                    self._dns, nameservers=self._dns_servers, port=self._dns_port
                )
                if self._dns or self._dns_servers
                else None,
            ) as http:
                verification = await http.post(
                    f"https://{host}/api/auth/login/2fa",
                    json={"challenge_id": challenge.challenge_id, "code": code.strip()},
                )
            if not verification.is_success:
                raise AuthenticationError(verification.status_code)
            data = verification.json()
        if not data.get("access_token"):
            raise ProtocolError("Login response lacks an access token")
        return Session(data["access_token"], host)


class BrowserAuth:
    """Optional Playwright provider; visible browser permits manual challenges.

    Bynix web app uses localStorage['token']. The result is subsequently validated
    by the client's /auth/me request, never assumed authenticated from the page URL.
    """

    def __init__(self, email=None, password=None, *, headless=False, timeout=180, profile_dir=None):
        self._email, self._password = email, password
        self.headless, self.timeout = headless, timeout
        self.profile_dir = Path(profile_dir).expanduser().resolve() if profile_dir else None

    async def __call__(self, host):
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = None
            context = None
            try:
                if self.profile_dir:
                    context = await p.chromium.launch_persistent_context(
                        str(self.profile_dir), headless=self.headless
                    )
                    page = await context.new_page()
                else:
                    browser = await p.chromium.launch(headless=self.headless)
                    context = await browser.new_context()
                    page = await context.new_page()
                await page.goto(f"https://{host}/login", wait_until="domcontentloaded")
                existing = await page.evaluate("localStorage.getItem('token')")
                if existing:
                    profile = await page.request.get(
                        f"https://{host}/api/auth/me",
                        headers={"Authorization": f"Bearer {existing}"},
                    )
                    if profile.status == 401:
                        await page.evaluate(
                            "localStorage.removeItem('token'); localStorage.removeItem('user')"
                        )
                        existing = None
                        await page.goto(f"https://{host}/login", wait_until="domcontentloaded")
                    elif profile.status != 200:
                        raise AuthenticationError(profile.status)
                if not existing and self._email and self._password:
                    await page.get_by_test_id("login-email").fill(self._email)
                    await page.get_by_test_id("login-password").fill(self._password)
                    await page.get_by_test_id("login-submit").click()
                await page.wait_for_function(
                    "Boolean(localStorage.getItem('token'))", timeout=self.timeout * 1000
                )
                token = await page.evaluate("localStorage.getItem('token')")
                return Session(token, host)
            finally:
                if context:
                    await context.close()
                if browser:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".pybynix-session-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump({"host": session.host, "token": session.token}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
