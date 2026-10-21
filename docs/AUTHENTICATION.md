# Authentication and session management

Choose a method that fits the deployment. Public market data can use `Bynix.public()`
without an account. Account data and orders require a Bynix access token.

## Interactive login

```console
pybynix login
pybynix doctor
pybynix balances
```

The terminal prompts for email and a hidden password. If requested by the platform,
it prompts for the verification code. A validated session is saved by default at
`~/.pybynix/session.json`. Choose another location with `--session PATH`.

```console
pybynix login --browser --session my-session.json
```

Browser login opens a dedicated browser so the owner can complete normal authentication.
Install the browser extra and Chromium first. `--profile-dir PATH` selects a dedicated
persistent profile for subsequent runs. Protect session and profile directories.

## Password provider and 2FA callback

```python
import asyncio
import getpass
import os
from pybynix import Bynix, PasswordAuth

async def verification_code(challenge):
    return getpass.getpass(f"{challenge.method} verification code: ")

async def main():
    provider = PasswordAuth(
        os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"],
        otp_callback=verification_code,
    )
    async with Bynix(provider=provider) as client:
        print(await client.get_balance())

asyncio.run(main())
```

The callback receives `LoginChallenge` with method and a private challenge identifier.
It returns the code string. The provider follows the observed `/auth/login/2fa` endpoint.
It does not read email automatically, solve CAPTCHA or bypass interactive checks.
The callback path is protocol-fixture-tested; the supplied live account did not trigger 2FA.

## Existing access token

```python
import asyncio
import os
from pybynix import Bynix

async def main():
    async with Bynix.from_token(os.environ["BYNIX_TOKEN"]) as client:
        print(await client.get_balance())

asyncio.run(main())
```

This token is the Bynix access token returned at ordinary login or stored by the web
application. A Socket.IO SID or Engine.IO SID is a connection identifier and cannot
replace it. `Session(token, host)` hides the token from its repr; do not log `.token`.

`Bynix().set_session(ssid=...)` offers familiar terminology for callers migrating from
other broker clients. It accepts the access token and must be used before account binding.

## Saved session with automatic refresh

```python
import asyncio
import os
from pathlib import Path
from pybynix import Bynix, PasswordAuth, save_session

async def main():
    path = Path.home() / ".pybynix" / "session.json"
    provider = PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"])
    async with Bynix.from_session_file(path, provider=provider) as client:
        print(await client.get_balance())
        save_session(client.session, path)

asyncio.run(main())
```

A rejected GET session can refresh once through the configured provider. Concurrent
requests share the refresh lock. The client checks account identity before replacing an
already-bound session. A profile-supplied refreshed token is accepted only if the request
used the still-current token, preventing older concurrent responses from overwriting it.

Without a configured provider, expiry is an explicit authentication error. Background
profile checks keep authenticated account subscriptions current when the platform rotates
tokens. Export `client.session` if a refreshed token should survive process restart.

## Browser provider and fallback chain

```python
import asyncio
import os
from pybynix import AuthChain, BrowserAuth, Bynix, PasswordAuth

async def main():
    email, password = os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"]
    provider = AuthChain(
        PasswordAuth(email, password),
        BrowserAuth(email, password, headless=False, profile_dir="browser-profile"),
    )
    async with Bynix(provider=provider) as client:
        print(await client.get_balance())

asyncio.run(main())
```

The chain falls back on explicit interactive challenges. It does not treat an invalid
password, blocked account or rate limit as a reason to rotate around the rejection.
A stored browser token is checked before reuse; a rejected token is cleared so the
configured fresh-login or owner-interactive flow can proceed.

## Custom provider

An async callable takes the selected host and returns `Session(token, host)`. Use this
for an application-owned session broker or secret store. PyBynix contains no hardcoded
third-party credential service and never sends credentials to an external provider by itself.

## Host and network binding

Imported sessions are bound to one hostname. For alternate-domain connection, use
`connect_any` with a provider that authenticates on the requested trusted host. Configure
the same proxy/DNS path on the provider and client. Browser traffic has separate routing
and is not claimed to inherit Python HTTP transport options.

Session JSON is written atomically with restrictive permissions where supported. It is
plaintext, not an encrypted vault; the containing directory's access controls matter on
Windows. Keep sessions, profiles, journals, `.env` files and raw captures outside public Git.
